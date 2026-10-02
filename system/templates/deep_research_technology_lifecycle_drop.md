# Technology Lifecycle Research Packet

> **Legacy rendered view.** Hunter (`system/research/HUNTER.md`) now produces
> one authoritative JSON packet with this lifecycle contract in `payload`.
> Calling/ingest functions may render this Markdown view for human review;
> Hunter must not independently author two potentially divergent records.

> Copy this template for each research cycle. Save the completed packet to
> `system/inbox/chatgpt_intelligence_drop/YYYY-MM-DD_HHMM_<brand-or-
> category>_tech-lifecycle.md`. Delete instructional text and omit empty
> optional fields before saving.
>
> **Also save a JSON sidecar with the exact same basename** (e.g.
> `2026-10-05_0930_mcdonalds-pos_tech-lifecycle.json` next to the `.md`
> above). The markdown is the human-readable evidence record; the JSON is
> what a future importer (Phase 1, not yet built) turns into real RBB
> records. Top-level key is deliberately `lifecycle_events` (not
> `findings`) so this packet type never collides with the existing
> competitor-platform-research importer that also reads this same inbox
> folder. Schema:
>
> ```json
> {
>   "packet_id": "tl-YYYYMMDD-HHMM-scope",
>   "schema": "rb.technology_lifecycle_research.v1",
>   "research_scope": "free text: brand, vendor, or category swept this cycle",
>   "source_ledger": [
>     {"url": "https://...", "source_type": "case study | press release | trade press | SEC filing | earnings call | investor presentation | franchisee communication | conference presentation | job posting | other", "productive": true, "notes": "optional"}
>   ],
>   "lifecycle_events": [
>     {
>       "event_id": "tle-<brand-slug>-<category>-<year>",
>       "brand_entity_id": "brand-<slug>, matching system/ecosystem_intelligence.json's existing id if the brand is already tracked there -- never invent a new id scheme",
>       "brand_name": "as a human-readable fallback if the entity id is uncertain",
>       "technology_category": "pos | pos_hardware | payments | back_office | inventory | labor_workforce | kds_kitchen_ops | loyalty | crm_cdp | mobile_apps | online_ordering | delivery_marketplace_orchestration | digital_menu_boards | drive_thru_ai | kiosks | voice_ai | computer_vision | automated_inventory | restaurant_ai_platform | ai_agents | predictive_operations",
>       "unit_count_at_decision": 0,
>       "scope": "corporate | franchise | both | unknown",
>       "previous_technology": {
>         "vendor": "", "product": "",
>         "deployment_date": "YYYY-MM-DD or a range, e.g. 2014 to 2016",
>         "deployment_date_confidence": "high | medium | low",
>         "estimated_tenure_years": null
>       },
>       "replacement_technology": {
>         "vendor": "", "product": "",
>         "decision_date": "", "announcement_date": "", "pilot_date": "",
>         "rollout_start_date": "", "rollout_completion_date": "",
>         "deployment_scope": "pilot | regional | systemwide | franchisee-optional | unknown"
>       },
>       "push_factors": [
>         {"factor": "e.g. technology EOL, poor reliability, high cost, integration limitations", "evidence": "", "source_url": "", "source_type": "", "confidence": "high | medium | low", "primary": true, "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown"}
>       ],
>       "pull_factors": [
>         {"factor": "e.g. cloud architecture, open APIs, lower cost, loyalty capabilities", "evidence": "", "source_url": "", "source_type": "", "confidence": "high | medium | low", "primary": true, "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown"}
>       ],
>       "pre_change_signals": [
>         {"t_offset_months": -12, "signal_type": "leadership_change | business_transformation | technology_signal | buying_signal", "description": "", "evidence": "", "source_url": "", "confidence": "high | medium | low", "evidence_type": ""}
>       ],
>       "pos_hardware_lifecycle": {
>         "applicable": false,
>         "hardware_manufacturer": "", "hardware_model": "",
>         "operating_system": "Windows | Android | Linux | proprietary | unknown",
>         "os_version": "", "os_eol_date": "", "hardware_eol_date": "",
>         "upgrade_in_place_possible": null,
>         "physical_life_note": "", "os_life_note": "",
>         "application_life_note": "", "economic_refresh_life_note": ""
>       },
>       "change_scope": "single_product | adjacent_bundle | platform_module_expansion | broad_stack_replacement",
>       "existing_stack_retained": [
>         {"technology_category": "", "vendor": "", "product": "", "note": "what stayed untouched when this replacement went in"}
>       ],
>       "phasing": [
>         {"phase": "pilot | regional_franchisee_deployment | enterprise_rollout | subsequent_module_adoption", "date_or_range": "", "scope_detail": "", "evidence": "", "source_url": "", "evidence_type": ""}
>       ],
>       "platform_relationship": {
>         "vendor_also_sells": [],
>         "categories_not_purchased_from_same_vendor": [],
>         "note": "did the winning vendor also sell categories the operator chose NOT to buy from them? that absence is itself the data point"
>       },
>       "follow_on_adoption": [
>         {"technology_category": "", "vendor": "same vendor as replacement_technology.vendor", "adopted_date_or_range": "", "months_after_initial_rollout": null, "evidence": "", "source_url": "", "confidence": "high | medium | low", "evidence_type": ""}
>       ],
>       "brand_maturity": "emerging_growth | established_regional | large_enterprise | unknown",
>       "migration_burden": {
>         "integrations_note": "", "franchisee_coordination_note": "",
>         "hardware_replacement_note": "", "training_note": "",
>         "data_migration_note": "", "payment_certification_note": "",
>         "overall_burden": "low | medium | high | unknown"
>       },
>       "outcome_research": [
>         {"horizon_months": 12, "state": "success_evidence_found | mixed_evidence | failure_or_abandonment_evidence | no_public_evidence_found", "evidence": "", "source_url": "", "evidence_type": ""}
>       ],
>       "is_non_switch_case": false,
>       "non_switch_note": "fill in if this is a renewed incumbent, canceled RFP, or abandoned pilot rather than a completed switch -- these are explicitly wanted, not noise",
>       "relationship_key_incumbent": {"brand_entity_id": "", "operator_entity_id": null, "technology_category": "", "vendor_entity_id": "", "product": ""},
>       "relationship_key_replacement": {"brand_entity_id": "", "operator_entity_id": null, "technology_category": "", "vendor_entity_id": "", "product": ""},
>       "change_economics": [
>         {"economic_factor": "contract_expiration | renewal_timing | price_increase | hardware_capex | implementation_expense | accelerated_depreciation | software_fees | technology_fees | training_expense | network_readiness_expense | payments_economics | processor_economics | vendor_subsidy | franchisor_subsidy | franchisee_contribution | legacy_support_expense | sunk_pilot_expense | opportunity_cost | organizational_support_intensity", "note": "", "evidence": "", "source_url": "", "confidence": "high | medium | low", "evidence_type": ""}
>       ],
>       "need_vs_willingness_vs_ability": {
>         "need_to_change": "", "willingness_to_change": "",
>         "ability_to_change": "", "economic_justification_to_change": ""
>       },
>       "migration_capacity_note": "free text -- location count, franchise org count/concentration, legacy tenure, integration dependencies, support resources, leadership bandwidth, concurrent projects, rollout deadline, financial capacity, prior implementation experience; do not reduce this to brand size",
>       "prior_implementation_scar": {
>         "has_scar": false, "description": "", "observed_effects": "",
>         "evidence": "", "source_url": "", "evidence_type": ""
>       },
>       "adoption_dimensions": {
>         "technical_performance": "positive | mixed | negative | not_evaluated | unknown",
>         "employee_acceptance": "positive | mixed | negative | not_evaluated | unknown",
>         "manager_acceptance": "positive | mixed | negative | not_evaluated | unknown",
>         "guest_acceptance": "positive | mixed | negative | not_evaluated | unknown",
>         "franchisee_acceptance": "positive | mixed | negative | not_evaluated | unknown",
>         "economic_outcome": "positive | mixed | negative | not_evaluated | unknown",
>         "operational_outcome": "positive | mixed | negative | not_evaluated | unknown"
>       },
>       "pilot_outcome": "expanded | extended | modified | paused | abandoned | not_scaled | replaced | no_public_evidence_of_scale | unknown | not_applicable",
>       "opportunity_classification": "displacement_opportunity | whitespace_opportunity | platform_expansion_opportunity | incumbent_defense | rollout_completion_opportunity | unknown",
>       "last_verified_current_date": "YYYY-MM-DD -- when you last confirmed this is still current, separate from when the underlying event happened",
>       "overall_confidence": "high | medium | low",
>       "visibility_class": "public_shared"
>     }
>   ],
>   "relationship_events": [
>     {
>       "event_id": "tre-<brand-slug>-<category>-<vendor-slug>-<descriptor>",
>       "relationship_key": {"brand_entity_id": "", "operator_entity_id": null, "technology_category": "", "vendor_entity_id": "", "product": ""},
>       "entity_level": "brand | operator | location",
>       "lifecycle_state": "discovery | evaluation | rfi | rfp | pilot | selected | contracted | rollout_planned | rollout_active | rollout_paused | rollout_restarted | rollout_scaled | deployed | operationalized | expanded | renewed | displaced | retired | pilot_abandoned | pilot_not_scaled | technology_reversal | vendor_platform_discontinued | strategic_divestiture_with_continued_use | historical_deployment_current_state_uncertain",
>       "state_date_or_range": "",
>       "scope_observation": {
>         "scope_type": "announced_scope | contracted_scope | mandated_scope | committed_scope | pilot_scope | installed_scope | live_scope | verified_scope",
>         "locations_low": null, "locations_high": null,
>         "unit_basis": "", "as_of": ""
>       },
>       "evidence": "", "source_url": "", "source_type": "",
>       "confidence": "high | medium | low",
>       "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown",
>       "observed_at": "YYYY-MM-DD",
>       "last_verified_current_date": "YYYY-MM-DD",
>       "visibility_class": "public_shared"
>     }
>   ],
>   "governance_records": [
>     {
>       "governance_id": "tg-<brand-slug>-<category>-<year>",
>       "brand_entity_id": "",
>       "technology_category": "",
>       "governance_state": "mandated | mandated_category_brand_selected_vendor | approved_vendor_list | preferred_not_required | franchisee_choice_with_requirements | grandfathered | new_store_mandate | pilot_optional | corporate_only | unknown",
>       "approved_vendors": [],
>       "franchisor_change_authority": {
>         "may_require_change_during_term": "yes | no | unknown",
>         "expense_borne_by": "franchisor | franchisee | shared | unknown",
>         "source": "",
>         "note": "a contractual right to require change later is NOT evidence a technology is currently mandated"
>       },
>       "fdd_sourced_fields": {
>         "fdd_year": "", "fdd_items_reviewed": [],
>         "required_technology": "", "required_hardware": "",
>         "technology_fees": "", "replacement_obligations": "",
>         "payment_requirements": "", "loyalty_requirements": "",
>         "online_ordering_requirements": "", "required_upgrades": "",
>         "differs_new_vs_existing_restaurants": ""
>       },
>       "evidence": "", "source_url": "",
>       "confidence": "high | medium | low",
>       "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown",
>       "observed_at": "YYYY-MM-DD",
>       "last_verified_current_date": "YYYY-MM-DD",
>       "visibility_class": "public_shared"
>     }
>   ],
>   "penetration_observations": [
>     {
>       "observation_id": "tp-<brand-slug>-<category>-<vendor-slug>-<yyyy-mm>",
>       "relationship_key": {"brand_entity_id": "", "operator_entity_id": null, "technology_category": "", "vendor_entity_id": "", "product": ""},
>       "entity_level": "brand | operator | location",
>       "penetration_type": "location_penetration | operator_penetration | system_sales_penetration | transaction_penetration | module_penetration",
>       "total_system_locations": null, "eligible_locations": null,
>       "contracted_locations": null, "mandated_locations": null,
>       "committed_locations": null, "pilot_locations": null,
>       "installed_locations": null, "live_locations": null,
>       "verified_locations": null,
>       "estimated_locations_low": null, "estimated_locations_high": null,
>       "penetration_pct_low": null, "penetration_pct_high": null,
>       "remaining_opportunity": null,
>       "rollout_velocity": "",
>       "observation_date": "YYYY-MM-DD", "last_verified_date": "YYYY-MM-DD",
>       "confidence": "high | medium | low",
>       "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown",
>       "source": "",
>       "visibility_class": "public_shared"
>     }
>   ],
>   "forcing_signals": [
>     {
>       "signal_id": "tfs-<brand-slug>-<category>-<yyyy-mm>",
>       "brand_entity_id": "brand-<slug>",
>       "technology_category": "same vocabulary as lifecycle_events.technology_category",
>       "forcing_event_type": "os_eol | hardware_eol | pos_software_eol | vendor_support_sunset | compliance | peripheral_incompatibility | franchise_mandate | leadership_change | other",
>       "detail": "",
>       "evidence": "", "source_url": "",
>       "confidence": "high | medium | low",
>       "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown",
>       "observed_at": "YYYY-MM-DD",
>       "visibility_class": "public_shared"
>     }
>   ]
> }
> ```
>
> **`relationship_events`, `governance_records`, and `penetration_observations`
> are new (post Cycles 7–8) and are often MORE valuable than a full
> `lifecycle_events` narrative**, because they're atomic and don't require
> a complete switch story to be worth recording. Use `relationship_events`
> every time you confirm a single lifecycle-state fact (e.g. "Vendor X was
> *selected* for Brand Y's POS in 2024" is a `selected` state with an
> `announced_scope` — record it even if you can't yet also reconstruct the
> full push/pull/outcome narrative for a `lifecycle_events` entry).
> `governance_records` captures FDD-sourced and other governance facts
> independent of any specific switch. `penetration_observations` captures
> any location/operator/system-sales/transaction/module penetration figure
> you find, even a single data point — never wait to have a complete time
> series before recording one observation. **Selection is not deployment**:
> never write a `live_scope`/`verified_scope` observation from a vendor's
> "selected for N restaurants" announcement — that's an `announced_scope`
> or `contracted_scope` observation instead, with its own `lifecycle_state`
> (`selected`/`contracted`), until independent/operator evidence confirms
> actual deployment.
>
> `forcing_signals` is for a brand's **current, not-yet-switched** stack —
> an observable condition (approaching OS/hardware EOL, a newly hired CTO
> with prior vendor experience, a digital-transformation announcement)
> that *might* precede a future switch but hasn't yet. Use this whenever
> research surfaces a brand that looks like it's approaching a change
> without yet having one to report as a `lifecycle_events` entry.
>
> **Two working hypotheses this dataset tests** (see `system/SCHEMAS.md` →
> "Technology Lifecycle & Change Events" → "Working hypotheses" for the
> full statement): (1) mature enterprise brands tend to modernize one
> layer at a time while younger/emerging brands are more likely to adopt
> a broader platform at once, and (2) a platform vendor is more likely to
> expand into adjacent categories *after* an initial win than to sell the
> whole stack up front. Record `change_scope`, `existing_stack_retained`,
> `platform_relationship`, `follow_on_adoption`, and `brand_maturity`
> accurately for every event regardless of which way the evidence points
> — these fields exist to test the hypotheses, not to confirm them. A
> vendor that *could* have sold the operator loyalty/payments/back office
> but didn't is exactly as important to record as one that did.
>
> `source_ledger` should list every page actually opened, including
> negative/unproductive ones (prevents re-researching the same dead end).
> `lifecycle_events` is the substantive payload — one entry per discovered
> event, whether it's a confirmed switch, a failed/abandoned one, or an
> explicit non-switch case. Every `evidence`/`source_url` pair must be
> real; never fabricate a citation to satisfy the schema. Every date
> without certainty should be a range in the markdown narrative below,
> with `deployment_date_confidence`/`confidence` reflecting that
> uncertainty in the JSON — don't collapse a range into a fake exact date
> just to fill the field.

## Cycle metadata

- Packet ID: `tl-YYYYMMDD-HHMM-scope`
- Research system: `Hunter`
- Started at: `YYYY-MM-DD HH:MM TZ`
- Completed at: `YYYY-MM-DD HH:MM TZ`
- Research scope (brand / vendor / category swept this cycle):
- Methodology: Vendor-discovery → brand/operator reconstruction →
  independent verification → pre-change reconstruction → outcome research
  (per `system/technology_lifecycle/RESEARCH_KICKOFF.md`)

## Executive result

- Pages reviewed:
- Candidate events identified:
- Events independently verified (promoted beyond vendor_claim):
- Non-switch / negative cases recorded:
- Conflicts found:
- Material takeaway:

## Event 1

- Brand:
- Technology category:
- Unit count at decision / scope (corporate vs. franchise):

### Previous technology
- Vendor / product:
- Deployment date (or range) and confidence:
- Estimated tenure at time of replacement:
- Source(s) for the above:

### Replacement technology
- Vendor / product:
- Decision / announcement / pilot / rollout-start / rollout-complete
  dates (ranges where exact dates aren't known):
- Deployment scope:

### Push factors (why leave the incumbent)
Repeat per factor — at minimum, name the factor, the evidence, the source,
confidence, whether it's primary, and the evidence type (vendor claim /
operator statement / independent evidence / inference / unknown).

- Factor:
- Evidence:
- Source URL / type / accessed date:
- Confidence:
- Primary factor? (yes/no)
- Evidence type:

### Pull factors (why choose the replacement)
Same structure as push factors, repeat per factor.

- Factor:
- Evidence:
- Source URL / type / accessed date:
- Confidence:
- Primary factor? (yes/no)
- Evidence type:

### Pre-change signal timeline (24–36 months prior)
Repeat per signal observed (leadership change, business transformation,
technology pain, buying signal). Leadership change alone is not proof of
impending change — note it, don't over-weight it.

- Approximate months before decision (T-minus):
- Signal type:
- Description:
- Evidence / source / confidence / evidence type:

### Change scope and stack composition
- Change scope (single product / adjacent bundle / platform module
  expansion / broad stack replacement):
- Brand maturity (emerging/growth / established regional / large
  enterprise):
- Existing stack retained (what categories/vendors stayed untouched by
  this change — e.g. kept the incumbent loyalty platform while replacing
  POS):
- Phasing (pilot → regional/franchisee deployment → enterprise rollout →
  any subsequent module adoption), with dates/ranges and evidence per
  phase:
- Platform relationship: did the winning vendor also sell other
  categories (payments, loyalty, back office, etc.) that the operator did
  **not** buy from them? Name them — the absence of consolidation is a
  real data point:
- Migration burden (integrations, franchisee coordination, hardware
  replacement, training, data migration, payment certification) and
  overall burden level:

### Follow-on adoption (same vendor, later category win)
If the same vendor won an additional category 12–36+ months after this
event, record it here with real evidence — don't assume it happened just
because the vendor sells that category too.

- Technology category adopted later:
- Approximate months after initial rollout:
- Evidence / source / confidence / evidence type:

### POS hardware lifecycle (only if this event is POS or POS hardware)
- Hardware manufacturer / model:
- Operating system (Windows / Android / Linux / proprietary) and version:
- OS EOL date, hardware EOL date (if known):
- Can the OS be upgraded without replacing hardware?
- Physical life / OS life / application life / economic-refresh life
  notes (these are four separate clocks — don't collapse them):

### Change economics
Repeat per factor actually evidenced (contract expiration, renewal timing,
price increase, hardware capex, implementation expense, software/
technology fees, training, data migration, payments/processor economics,
vendor/franchisor subsidy, franchisee contribution, legacy support
expense, sunk pilot expense, opportunity cost, organizational support
intensity). Don't force every factor — only record what's evidenced.

- Economic factor:
- Note / evidence / source / confidence / evidence type:

### Need, willingness, ability, and economic justification to change
These are four separate questions — a bad incumbent does not automatically
create a sales opportunity, and an adequate incumbent can become
vulnerable for reasons unrelated to dissatisfaction (contract expiration,
price increase, hardware EOL, leadership change, acquisition,
transformation initiative, franchisee pressure, expansion).

- Need to change:
- Willingness to change:
- Ability to change:
- Economic justification to change:

### Migration capacity
Do not reduce this to brand size — a large system can move quickly with
narrow scope and intensive support; a small system can move slowly under
difficult governance or economics.

- Migration capacity note (location count, franchise org count/
  concentration, legacy tenure, integration dependencies, support
  resources, leadership bandwidth, concurrent transformation projects,
  rollout deadline, financial capacity, prior implementation experience):

### Prior implementation scar
A previous failed or difficult project at this brand can materially affect
this decision's pace and governance (longer pilots, more oversight,
reference demands, contractual protections, reluctance to change).

- Has a prior scar? (yes/no):
- Description:
- Observed effects on this decision:
- Evidence / source / evidence type:

### Adoption assessment (multidimensional — do not collapse to success/fail)
Rate each dimension separately where evidence allows: positive / mixed /
negative / not evaluated / unknown. Technical success does not imply
adoption success (e.g. a system can work reliably but be rejected on
guest or franchisee acceptance).

- Technical performance:
- Employee acceptance:
- Manager acceptance:
- Guest acceptance:
- Franchisee acceptance:
- Economic outcome:
- Operational outcome:

### Pilot outcome (if this event involved a pilot)
One of: expanded / extended / modified / paused / abandoned / not scaled /
replaced / no public evidence of scale / unknown / not applicable. A pilot
that didn't scale is not automatically a failure — use "no public evidence
of scale" rather than assuming failure from silence.

- Pilot outcome:

### Opportunity classification (for Genius)
One of: displacement opportunity / whitespace opportunity / platform
expansion opportunity / incumbent defense / rollout completion opportunity
/ unknown. An incumbent competitor winning this layer doesn't necessarily
close the account — classify what it actually means.

- Opportunity classification:

### Outcome research
Repeat per horizon (6 / 12 / 24 / 36 months). Use
`no_public_evidence_found` rather than assuming success from silence.

- Horizon (months):
- Outcome state:
- Evidence / source / evidence type:

### Last verified current
Historical announcements go stale, especially for fast-moving categories
(AI, robotics, payments). State when you last confirmed this event's
facts are still current, separate from when the event itself happened.

- Last verified current (date):

### Is this a non-switch case?
If this "event" is actually a renewed incumbent, a canceled RFP, an
abandoned pilot, or franchisee resistance that stopped a rollout — say so
explicitly here. These are wanted, not noise.

- Non-switch note:

### Overall confidence for this event:

---

_Repeat the "## Event N" block for each additional event found this
cycle._

## Standalone forcing signals (brands not yet confirmed switching)

Use this section for a brand that shows an observable forcing condition on
its *current* stack but has no completed or confirmed switch to report as
an Event above — an approaching OS/hardware EOL, a new CTO with prior
vendor experience, a digital-transformation announcement, a relevant job
posting. Repeat per signal.

- Brand:
- Technology category:
- Forcing event type:
- Detail:
- Evidence / source URL / accessed date:
- Confidence:
- Evidence type:
- Observed at (date):

## Standalone relationship-state observations

Use this for any single, atomic lifecycle-state or scope fact you can
confirm, even without a complete switch narrative — e.g. "Vendor X was
selected for Brand Y's POS, announced scope 7,000 restaurants" is worth
recording on its own. Remember: selection is not deployment — state the
`lifecycle_state` (discovery / evaluation / RFI / RFP / pilot / selected /
contracted / rollout_planned / rollout_active / rollout_paused /
rollout_restarted / rollout_scaled / deployed / operationalized /
expanded / renewed / displaced / retired / pilot_abandoned /
pilot_not_scaled / technology_reversal / vendor_platform_discontinued /
strategic_divestiture_with_continued_use /
historical_deployment_current_state_uncertain) and the specific scope type
(announced / contracted / mandated / committed / pilot / installed / live
/ verified scope) separately — never collapse "selected" into "deployed."
Repeat per observation.

- Brand / operator (if operator-specific) / technology category / vendor
  / product:
- Entity level (brand / operator / location):
- Lifecycle state:
- State date or range:
- Scope type and location count (or range), unit basis, as-of date:
- Evidence / source / source type / confidence / evidence type:
- Last verified current (date):

## Governance records

Use this for franchise-governance facts, ideally FDD-sourced (Items 5, 6,
7, 8, 11). Remember: a franchisor's contractual *authority* to require a
future change is not evidence that a technology is *currently* mandated —
keep those separate. Repeat per brand/category.

- Brand / technology category:
- Governance state (mandated / mandated_category_brand_selected_vendor /
  approved_vendor_list / preferred_not_required /
  franchisee_choice_with_requirements / grandfathered / new_store_mandate
  / pilot_optional / corporate_only / unknown):
- Approved vendors (if an approved-vendor-list model):
- Franchisor change authority: may the franchisor require a change during
  the term? Who bears the expense? Source citation:
- FDD-sourced fields (year, items reviewed, required technology, required
  hardware, technology fees, replacement obligations, payment/loyalty/
  online-ordering requirements, required upgrades, differs for new vs.
  existing restaurants):
- Evidence / source / confidence / evidence type:
- Last verified current (date):

## Penetration observations

Record any location/operator/system-sales/transaction/module penetration
figure you find — even a single data point is worth recording; don't wait
for a complete time series. Distinguish brand-level from operator-level
(an operator's footprint is evidence toward, not equal to, the brand
figure), and distinguish an enterprise-standard win from verified
installed-base penetration — both can be true simultaneously for the same
brand/category. Never fabricate a figure; use a range when evidence is
partial.

- Brand / operator (if applicable) / technology category / vendor /
  product:
- Entity level (brand / operator / location):
- Penetration type (location / operator / system-sales / transaction /
  module penetration):
- Total system locations / eligible locations (if known):
- Contracted / mandated / committed / pilot / installed / live / verified
  location counts (whichever are evidenced):
- Estimated location range (low–high) and penetration percentage range,
  if exact counts aren't available:
- Remaining opportunity / rollout velocity, if determinable:
- Observation date / last verified date:
- Confidence / evidence type / source:

## Negative findings

Record candidate events that were checked but could not be substantiated
beyond vendor marketing, or where independent sources contradicted the
vendor's claim.

- Candidate or claim:
- Pages checked:
- Result:

## Source ledger

List every page actually opened, including useful negative results.

| URL | Source type | Accessed | Supports event(s) | Notes |
|---|---|---|---|---|

## Research notes and inferences

Keep inference clearly separate from sourced observations. Label every
inference as `rbb_inference` in the JSON sidecar and never present it as a
canonical fact here either.
