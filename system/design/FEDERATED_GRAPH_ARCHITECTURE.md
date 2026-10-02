# Federated Graph Architecture

**Date:** 2026-05-27  
**Status:** Design artifact — implementation follows after spreadsheet/topology ingestion support  
**Trigger artifact:** `NSN Lookup 2026-05 MAY.xlsx` McDonald's operational workbook

---

## Purpose

RB must not collapse every intelligence artifact into one giant relationship graph. That would pollute relationship intelligence with operational topology, mix confidence models, and make traversal noisy at scale.

The correct architecture is federated graph intelligence: separate graph layers with explicit connectors between them.

The rule:

> Graphs connect. They do not merge.

---

## Graph Layers

### 1. Macro Industry Graph

High-level ecosystem intelligence.

Contains:
- companies
- executives
- vendors
- products
- industry trends
- acquisitions
- partnerships
- market positioning
- strategic narratives
- industry influence

Examples:
- PAR Technology
- Toast
- Global Payments
- restaurant technology landscape
- AI operational trends
- market positioning shifts

Purpose:

> What is happening in the industry?

This graph supports strategic landscape understanding.

### 2. Micro Ecosystem Graph

Localized operational topology for a specific system, account, market, operator, or enterprise ecosystem.

Contains:
- operational structures
- role nodes
- field relationships
- market mappings
- region and field-office governance
- deployment relationships
- operational accountability chains
- influence paths
- store/unit mappings when relevant

Example:
- McDonald's NSN / StoreTech / market / OTM / STIM / RFM topology extracted from a multi-tab workbook

Purpose:

> How does this specific system actually function operationally?

This graph supports execution intelligence. It is not general restaurant industry intelligence.

### 3. Personal Relationship Graph

Todd's direct relationship universe.

Contains:
- RB contact profiles
- RI events
- trust / tier / DRR state
- meeting history
- communication cadence
- loops and obligations
- opportunity status
- influence proximity
- relationship momentum

Purpose:

> Who does Todd know, what is the state of those relationships, and what should he do next?

This graph supports human strategic navigation.

### 4. Opportunity / Thread Graph

Active strategic situations.

Contains:
- active threads
- opportunity objects
- loop state
- decision points
- stakeholders
- timing windows
- action candidates
- closure criteria

Purpose:

> What is moving now, and what action is due?

This graph connects to personal relationships and may activate macro or micro context, but it is not a replacement for either.

### 5. Signal Graph

Short-lived observations and evidence.

Contains:
- market signals
- relationship signals
- source health
- freshness state
- evidence snippets
- confidence assessments
- provenance links

Purpose:

> What changed, how fresh is it, and how much can RB trust it?

Signals may promote into other graphs only through explicit review, confidence, and provenance rules.

---

## Connector Model

Federated graphs connect through typed cross-graph references, not by copying nodes wholesale.

Common connector fields:
- `entity_id`
- `external_entity_key`
- `profile_id`
- `role_node_id`
- `ecosystem_graph_id`
- `source_ref`
- `confidence`
- `provenance`
- `valid_from`
- `valid_to`

Example:

```json
{
  "connector_id": "cx-mcd-role-to-profile-example",
  "from_graph": "personal_relationship",
  "from_id": "profile-jane-doe",
  "to_graph": "micro_ecosystem:mcdonalds_us_ops",
  "to_id": "role:field-office:chicago:stim",
  "relation": "person_maps_to_operational_role",
  "confidence": {"level": "medium", "score": 0.68},
  "provenance": ["src:nsn_lookup_2026_05_may.xlsx", "src:profile_company_match"],
  "valid_from": "2026-05-04"
}
```

The personal profile remains a personal profile. The operational role remains a role node. The connector is the bridge.

---

## Dynamic Activation

Micro graphs should be dynamically activated by context.

Examples:
- User asks about McDonald's deployment strategy → activate the McDonald's micro ecosystem graph.
- User asks about broad restaurant AI trends → keep McDonald's micro topology dormant unless a specific McDonald's relationship or thread is relevant.
- Daily brief has no McDonald's signal → do not traverse the McDonald's micro graph.
- An active thread mentions McDonald's field rollout → activate only the relevant micro subgraph: market, field office, role, and influence path.

For the Custom GPT/API surface, activation should be implicit. If a user question mentions McDonald's, McDonalds, MCD, NSN, StoreTech, store/site lookup, operator/entity counts, franchisee/operator structure, field office, market, coop/co-op, OTM, STIM, RFM, FBP, OTP, deployment topology, field support, or operational accountability, RB should call `getMicroGraphSummary(query="McDonald's")` before answering and use the returned evidence when relevant.

Activation exists to protect:
- token economy
- cognitive clarity
- inference quality
- hallucination resistance
- user trust

---

## Raw Uploads Are Not Intelligence Artifacts

Uploaded files are source material, not the durable intelligence object.

Correct pipeline:

```text
Raw Upload
  -> Extraction
  -> Normalization
  -> Entity Resolution
  -> Topology Construction
  -> Graph Artifact Creation
  -> Confidence + Provenance Layering
  -> Strategic Queryability
```

For topology-heavy spreadsheets, RB should not keep answering from the workbook as if the workbook were the product. The workbook should become normalized nodes, edges, source references, and confidence records.

The raw file may be retained only under retention/provenance policy. The queryable artifact is the graph projection.

The source material and the projection are tenant-owned. A user's commercial
datasets, customer/account topology, purchased market data, and derived graph
artifacts stay inside that user's RB instance. They must not become product seed
graphs for other users. Other users get the same extraction pipeline and schema,
then build their own macro, micro, personal, opportunity, and signal graphs from
their authorized onboarding sources and ongoing usage.

---

## McDonald's NSN Workbook Classification

The `NSN Lookup 2026-05 MAY.xlsx` upload is a micro ecosystem artifact.

It is not:
- a generic spreadsheet attachment
- a contact list
- a restaurant industry trend source
- a personal relationship graph update by itself

It is:

> Micro-topology intelligence for a specific operational ecosystem.

Likely graph model:
- Graph ID: `micro_ecosystem:mcdonalds_us_ops`
- Node classes: `store`, `market`, `coop`, `field_office`, `role`, `person`, `entity`, `fbp`, `lookup_view`
- Edge classes: `belongs_to_market`, `belongs_to_coop`, `covered_by_field_office`, `accountable_to_role`, `mapped_to_person`, `finance_business_partner_for`, `source_row_for`
- Source references: workbook, sheet, row, column, extracted_at, file hash
- Confidence: high for direct tabular mappings; medium for formula-derived values; low for inferred role equivalence

Workbook tabs should be treated as source tables feeding one topology graph:
- `NSN-Lookup` — lookup/output view; formula-derived, not the canonical source table
- `StoreTech` — store/site operational base table
- `FO OTM-STIM` — field office to role/person mapping
- `Markets` — coop/market/field office/role mapping
- `RFM` — NSN to coop mapping source
- `FBP` — finance partner mapping source
- `Entity` — entity to FBP mapping source
- `COOP2` — store to coop mapping source

---

## Confidence Separation

Each graph layer needs its own confidence model.

Macro confidence:
- source credibility
- corroboration count
- channel diversity
- recency
- public verifiability

Micro topology confidence:
- source table authority
- row completeness
- formula vs literal value
- sheet-to-sheet consistency
- timestamp/version of the operational file
- conflict rate between tabs

Personal relationship confidence:
- direct interaction evidence
- source freshness
- identity match strength
- Todd confirmation
- relationship signal strength

Do not let confidence in one layer contaminate another. A high-confidence McDonald's store-to-market mapping does not imply high-confidence relationship proximity. A high-confidence Todd relationship does not imply authority over the operational topology.

---

## Query Rules

RB should choose graph layers by question type.

Macro questions:
- "What is happening in restaurant AI?"
- "How is Toast positioned?"
- "What does the PAR/Brink landscape look like?"

Use macro industry graph.

Micro questions:
- "How does McDonald's field support appear to be organized?"
- "Which field office or market maps to this NSN?"
- "Who appears accountable for this operational slice?"

Use micro ecosystem graph.

Personal relationship questions:
- "Who do I know at McDonald's?"
- "Who can introduce me to someone near this team?"
- "What is my last touch with this person?"

Use personal relationship graph, optionally connected to a micro role node.

Opportunity questions:
- "What should I do next on this McDonald's thread?"
- "Who matters for this deployment conversation?"

Use opportunity/thread graph, then activate personal and micro graph context as needed.

---

## Anti-Patterns

Do not:
- import every row from a topology workbook into the personal relationship graph
- treat every operational person as an RB contact
- promote spreadsheet names to durable relationship records without RI evidence
- use macro vendor confidence rules for operational topology
- load every micro ecosystem graph into daily context
- answer from raw workbook tabs when a normalized graph projection exists
- flatten store, market, coop, field office, role, and person into one undifferentiated entity list

---

## Implementation Implications

RB needs a new ingestion route for topology-heavy spreadsheets.

Minimum future components:
- artifact classifier: `.xlsx` multi-tab topology detection
- workbook extractor: sheet inventory, row counts, headers, formula/literal distinction
- topology normalizer: typed nodes and edges
- entity resolver: map people/org names to known RB profiles and ecosystem entities without merging them
- micro graph storage: separate file namespace from `baseline_index.json` and `ecosystem_intelligence.json`
- activation index: query terms and active-thread hooks that decide when to load a micro graph
- provenance index: file hash, sheet, row, column, extraction timestamp

Candidate storage layout:

```text
system/graphs/
  macro/
    restaurant_industry.json
  micro/
    mcdonalds_us_ops/
      graph.json
      sources.json
      activation.json
      README.md
  overlays/
    personal_to_micro.json
    personal_to_macro.json
```

---

## Relationship To Existing Designs

This design refines, but does not replace:
- `system/ecosystem_intelligence.json` — current macro/operator ecosystem graph
- `system/design/RI_OVERLAY_DESIGN.md` — user relevance overlay for ecosystem entities
- `baseline_index.json` — personal relationship graph
- `strategic_operators.yaml` — persistent strategic-operator lane

The key correction is that `ecosystem_intelligence.json` should not become a dumping ground for every operational topology artifact. Macro ecosystem intelligence and micro ecosystem topology need separate graph scopes.

---

## Open Questions

1. Should each micro ecosystem graph have its own schema, or one shared `rb_micro_ecosystem_graph_v1` schema with domain extensions?
2. Should topology graph extraction preserve all store-level rows, or strategically compress low-value row detail into indexed lookup tables?
3. What threshold promotes an operational person node into the personal relationship graph? Proposed: never automatically; only RI evidence or Todd confirmation.
4. Should micro graph activation be query-time only, or also triggered by active-thread and daily-brief signal scans?
5. How should RB expire or version operational topology when a newer workbook arrives?
