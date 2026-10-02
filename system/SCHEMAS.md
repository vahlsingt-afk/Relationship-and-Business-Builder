# Schemas

Single source of truth for the shape of every canonical artifact. Concrete examples, not abstract field lists. If you need to change a schema, change it here first, then propagate.

## Conventions

- IDs are lowercase, hyphenated, stable. Person IDs use `firstname-lastname` (disambiguate with employer/year suffix when needed). Company IDs use `company-name`.
- Dates are `YYYY-MM-DD` (no times, unless capturing a meeting timestamp explicitly).
- Markdown files use YAML frontmatter for structured fields and free prose for the body.
- All structured fields that don't apply use explicit `null`, never empty strings or absent keys.

---

## Relationship Intelligence Event — planned `ri_events/*.jsonl`

Planned authoritative event stream. One immutable event per relationship-intelligence signal. Current projections (`baseline_index.json`, `cards/`, `loop_ledger.md`, `active_threads.yaml`, `today.md`) should eventually be derivable from this stream plus explicit user confirmations.

```json
{
  "event_id": "ri_2026-05-18_ish-singh_maho-next-steps_001",
  "captured_at": "2026-05-18T10:30:00-05:00",
  "event_at": "2026-05-18T07:16:10-05:00",
  "source": {
    "type": "email_thread",
    "id": "gmail_thread_19de49aca99208bf",
    "path": null,
    "title": "Re: Next Steps"
  },
  "entities": {
    "people": [
      {
        "raw": "Ish Singh",
        "matched_id": "ish-singh",
        "match_confidence": 0.98,
        "decision": "matched_existing"
      }
    ],
    "companies": [
      {
        "raw": "Maho",
        "matched_id": null,
        "decision": "company_alias_pending"
      }
    ]
  },
  "dedupe": {
    "dedupe_key": "email:gmail_thread_19de49aca99208bf:2026-05-18T07:16:10-05:00",
    "decision": "new_event",
    "duplicates": []
  },
  "signal": {
    "type": "inbound_opportunity_signal",
    "direction": "inbound",
    "substance": "high",
    "confidence": 0.9,
    "strategic_relevance_impact": 0.8,
    "relationship_warmth_impact": 0.7,
    "recommended_projection_changes": [
      "promote LMI to LKI",
      "update last_touch",
      "open active thread",
      "open scheduling loop"
    ]
  },
  "persistence": {
    "status": "verified",
    "projected_to": [
      "baseline_index.json",
      "briefs/2026-05-18-ish-singh-maho-next-steps.md",
      "active_threads.yaml",
      "loop_ledger.md"
    ],
    "validated_at": "2026-05-18T10:35:00-05:00"
  }
}
```

Required semantics:

- Events are append-only. Never edit an existing event; write a corrective event.
- `captured_at` is when RB recorded the event.
- `event_at` is when the relationship signal actually occurred.
- `source` points to the feed item, artifact, transcript, email thread, or manual note.
- `entities` stores raw names and match decisions, not only final IDs.
- `dedupe` records why this event is new or linked to a prior event.
- `signal` stores relationship meaning and projection recommendations.
- `persistence` records whether the event actually changed canonical projections and whether validation passed.

---

## Persistent Strategic Intelligence Memory - `strategic_memory.json`

Durable market, company, POV, watchlist, opportunity, and daily-brief weighting memory. This is for strategic intelligence Todd explicitly marks as reusable, not ordinary chat context.

```json
{
  "schema": "rb_persistent_strategic_memory_v1",
  "updated_at": "2026-05-22T09:00:00",
  "signals": [
    {
      "id": "si_6b3f0d7a84c091",
      "created_at": "2026-05-22T09:00:00",
      "last_seen_at": "2026-05-22T09:00:00",
      "source": {"type": "manual_user_input"},
      "categories": [
        "static_industry_assessment",
        "market_pattern",
        "daily_brief_weighting"
      ],
      "signal": "Starbucks discontinued an AI inventory tool across North America after operational accuracy and adoption issues.",
      "what_it_proves": "Restaurant AI must survive real-world operating conditions, not just demos or pilots.",
      "strategic_implication": "Enterprise buyers will increasingly scrutinize AI claims around reliability, workflow burden, ROI, and operator trust.",
      "user_pov_alignment": "Supports Todd's thesis that restaurant tech succeeds only when it is simple, trusted, operationally durable, and economically provable.",
      "future_use": [
        "AI restaurant tech vendors",
        "computer vision companies",
        "inventory platforms",
        "enterprise rollout claims",
        "restaurant tech investment narratives",
        "LinkedIn post opportunities",
        "daily brief market intelligence",
        "relationship opportunities"
      ],
      "tags": [
        "AI",
        "restaurant technology",
        "computer vision",
        "inventory automation",
        "enterprise rollout failure",
        "operator trust",
        "ROI skepticism",
        "Starbucks",
        "operational resilience"
      ],
      "entities": ["Starbucks"],
      "weighting": {
        "urgency": "medium",
        "relevance": "high",
        "strategic_value": "high",
        "relationship_opportunity": "medium",
        "market_risk": "high",
        "content_opportunity": "high",
        "company_watchlist_importance": "high"
      },
      "raw_excerpt": "CNBC article excerpt...",
      "reuse_count": 0
    }
  ]
}
```

Required semantics:

- Only write here when a durable-memory trigger is present, such as `industry intelligence`, `note this`, `remember this`, `this validates`, `watchlist`, `strategic company`, or `static memory`.
- Store the core lesson and future reuse requirements, not only a pasted article summary.
- Categories may include `user_pov`, `static_industry_assessment`, `strategic_company_watchlist`, `market_pattern`, `relationship_opportunity`, and `daily_brief_weighting`.
- Daily briefs and company assessments should load this file and reuse matching signals when relevant.
- User-facing responses must use action language: `RB recorded`, `RB updated`, or `RB linked this signal to...`.

---

## Ecosystem Intelligence Graph — `ecosystem_intelligence.json`

Persistent, mutable ecosystem intelligence layer. It is industry-agnostic: restaurants are the first domain pack, not the core schema. The graph should eventually support questions like "Which Top 1500 restaurant brands use PAR for POS?" while preserving confidence, source attribution, relationship coverage, risk, and strategic interpretation.

```json
{
  "version": 1,
  "contract": "rb_ecosystem_intelligence_v1",
  "last_updated": "2026-05-27",
  "domain_packs": ["restaurants"],
  "entities": [
    {
      "id": "brand-burger-king",
      "name": "Burger King",
      "entity_type": "brand",
      "subtype": "restaurant_brand",
      "status": "active",
      "domains": ["restaurants"],
      "aliases": [],
      "attributes": {
        "rank": 3,
        "segment": "QSR Burger",
        "system_sales": 11000000000,
        "unit_count": 7200,
        "auv": 1527778
      },
      "sources": ["src-technomic-top-1500-2024"],
      "confidence": {"level": "high", "score": null, "rationale": "Imported from operator-supplied restaurant baseline file.", "review_after": null},
      "notes": "",
      "created_at": "2026-05-27T09:00:00",
      "updated_at": "2026-05-27T09:00:00",
      "strategic_narratives": [
        {
          "id": "narrative-hungry-howie-tech-stack-modernization",
          "narrative_type": "tech_stack_modernization",
          "status": "active",
          "confidence": "high",
          "supporting_signals": [
            {
              "category": "pos",
              "signal_type": "vendor_selected",
              "description": "Hungry Howie confirmed as Toast customer (pos)",
              "source": {"title": "...", "url": "...", "date": "2026-06-15"},
              "added_at": "2026-06-15T18:00:00+00:00"
            },
            {
              "category": "back_office",
              "signal_type": "vendor_selected",
              "description": "Hungry Howie confirmed as Restaurant365 customer (back_office)",
              "source": {"title": "...", "url": "...", "date": "2026-06-15"},
              "added_at": "2026-06-15T18:05:00+00:00"
            },
            {
              "category": "loyalty",
              "signal_type": "sunset",
              "description": "Hungry Howie sunset of loyalty platform",
              "source": {"title": "...", "url": "...", "date": "2026-06-15"},
              "added_at": "2026-06-15T18:05:00+00:00"
            }
          ],
          "next_expected_signals": ["loyalty platform replacement announcement"],
          "last_updated": "2026-06-15T18:05:00+00:00"
        }
      ]
    }
  ],
  "relationships": [
    {
      "id": "rel-brand-burger-king-pos-vendor-par-technology",
      "from_entity_id": "brand-burger-king",
      "to_entity_id": "vendor-par-technology",
      "relationship_type": "uses_vendor_for_category",
      "status": "active",
      "domains": ["restaurants"],
      "category": "pos",
      "product": "Brink POS",
      "deployment": {
        "stage": "active_rollout",
        "deployed_units": 3000,
        "target_units": null,
        "target_date": "2026-12-31",
        "penetration_pct": null,
        "scope": "",
        "evidence": "PAR announcement"
      },
      "evidence_posture": "partially_substantiated",
      "interpretation_scope": "POS software deployment signal; confirm whether this is system-of-record POS, hardware, payments, regional, franchisee, or pilot scope.",
      "risk": "yellow",
      "sources": ["src-par-announcement"],
      "confidence": {"level": "medium", "score": null, "rationale": "Vendor relationship imported from sourced evidence row.", "review_after": null},
      "strategic_note": "Large franchise deployment; adoption success depends on franchisee operational execution.",
      "created_at": "2026-05-27T09:00:00",
      "updated_at": "2026-05-27T09:00:00"
    }
  ],
  "signals": [],
  "assessments": [],
  "sources": [],
  "user_relevance": [],
  "strategic_recommendations": []
}
```

Required semantics:

- Core primitives are universal: `Entity`, `Relationship`, `Signal`, `Assessment`, `Confidence`, `Source`, `User relevance`, and `Strategic recommendation`.
- Domain-specific vocabulary lives in `domain_packs/{domain}.json`. Restaurant-specific concepts such as `AUV`, `KDS`, `drive_thru_systems`, `franchisee_sentiment`, and `integration_fatigue` must not be hardcoded into the universal schema.
- Technomic Top 1500 files become restaurant brand entities with baseline metrics; vendor evidence rows become relationships/deployments with risk, confidence, and source fields.
- Vendor-category uploads are provisional by default. RB must distinguish a vendor's exact role: system-of-record software, approved hardware, payments/acquiring, integration partner, regional deployment, franchisee subset, pilot, legacy incumbent, or replacement signal.
- For restaurant POS, logos and vendor-list appearances do not settle the question. Example: McDonald's may surface NCR as an approved vendor/hardware signal while NewPOS remains the owned POS system context. Preserve both facts with scope and evidence rather than flattening them.
- The graph is not a dashboard dump. Records should preserve strategic interpretation and relationship relevance when known, and should state `unknown` rather than inventing coverage.
- Passive uploads and external posts must pass through `passive_intelligence.py` / `evaluatePassiveIntelligence` before RB treats them as intelligence. The evaluation extracts factual claims, classifies source authority and commercial incentive exposure, checks existing ecosystem evidence for corroboration/contradiction, and assigns `claim_status` plus `graph_mutation_eligibility`.
- Ecosystem `signals[]` may carry passive-intelligence metadata: `source_type`, `source_quality`, `confidence_score`, `corroboration_count`, `corroboration_sources`, `claim_status`, `verification_timestamp`, `narrative_classification`, `graph_mutation_eligibility`, and `strategic_relevance_score`.
- Canonical fact layers should only use `graph_mutation_eligibility=canonical_fact` or `corroborated_intelligence`. Weak, speculative, disputed, anecdotal, or marketing-adjacent inputs remain signals/narratives and must not silently promote relationships or entity facts.
- Validate with `python3 system/schemas/validate.py --ecosystem-only`.
- Ingest/query via `python3 system/scripts/ecosystem_intelligence.py`.

### Strategic Narratives — `entities[].strategic_narratives[]` (RB 9.89, RB-DEFECT-046 Slice 2)

Brand entities may carry a `strategic_narratives[]` array — the persisted
"institutional memory" that accumulates across articles about the same brand,
so a sequence of independent signals (vendor selections, category
sunsets/replacements) is recognized as a single ongoing story rather than
re-discovered from scratch each time.

- `id`: `narrative-{brand-slug}-{narrative_type-with-dashes}`.
- `narrative_type`: currently only `tech_stack_modernization`.
- `status`: `active` (only one active narrative per `narrative_type` per
  brand; future signals append to it rather than creating duplicates).
- `confidence`: `low` / `medium` / `high`, derived from the count of distinct
  tech-stack categories (`pos`, `back_office`, `loyalty`, `online_ordering`,
  `payments`, `workforce`, `voice_ai`, `automation`) represented in
  `supporting_signals` — 1 category = `low`, 2 = `medium`, 3+ = `high`.
- `supporting_signals[]`: one entry per contributing mutation —
  `category`, `signal_type` (`vendor_selected` | `sunset`), `description`,
  `source`, `added_at`.
- `next_expected_signals[]`: human-readable predictions, recomputed on every
  update — an unresolved `sunset` category projects a "{category} replacement
  announcement"; once >= 2 categories are represented, every remaining
  tracked category projects "{category} selection or change".
- `last_updated`: ISO timestamp of the most recent supporting signal.
- Managed by `update_strategic_narratives()` and
  `_get_or_create_brand_entity()` in `system/scripts/intelligence_mutation_engine.py`,
  invoked from `apply_mutations()` for `vendor_customer_relationship` and
  `category_lifecycle_signal` mutations.

### Ecosystem Confidence Model

Every entity and relationship carries a `confidence` block:

```json
{
  "level": "high | medium | low | unknown",
  "score": 0.0,
  "rationale": "Human-readable explanation of why this confidence level was assigned.",
  "review_after": "2026-12-31"
}
```

`level` is the canonical gate for display and promotion decisions. `score` is optional numeric precision when available (0.0–1.0). `rationale` must be human-readable. `review_after` is an ISO date after which the record should be re-evaluated.

Valid `evidence_posture` values for relationships:
- `substantiated` — multiple independent sources confirm
- `partially_substantiated` — single credible source, not corroborated
- `unverified` — vendor claim or indirect signal, no ecosystem corroboration
- `disputed` — contradictory evidence exists
- `stale` — was substantiated but review_after has passed

Sticky substantiated relationships: once a relationship reaches `substantiated`, it must not be automatically downgraded by a single new conflicting signal. Disputes add a `risk` flag (`red | yellow`) and flag for human review rather than overwriting the existing record.

---

### Relationship Classification Model — `relationships[].relationship_classification`

A vendor appearing as a "customer logo" on a restaurant brand's website can mean anything from a single 8-unit franchisee pilot to a 14,000-unit corporate mandate. RB classifies every `uses_vendor_for_category` relationship into one of six levels of strategic significance, so logo-based intelligence does not get mistaken for deployment-based intelligence. Store count and system penetration are weighted more heavily than the mere existence of a relationship.

| Level | `level_name` | Description | Confidence band |
|---|---|---|---|
| 1 | `pilot` | Limited test, proof of concept, evaluation deployment, temporary trial | Low |
| 2 | `single_franchisee` | One operator group, limited store count, no franchisor involvement | Low-Medium |
| 3 | `multi_franchisee_adoption` | Multiple operator groups, repeatable deployment pattern, growing footprint | Medium |
| 4 | `preferred_vendor` | Recommended by franchisor, approved integration partner, conference participation, preferred pricing | Medium-High |
| 5 | `standardized_platform` | Corporate standard, required for new locations, mandated conversion path, system-wide deployment | High |
| 6 | `strategic_platform` | Mission-critical infrastructure, deep integration into operating model, joint roadmap development, difficult to replace | Very High |

```json
{
  "level": 3,
  "level_name": "multi_franchisee_adoption",
  "confidence": {"level": "medium", "score": 0.5, "rationale": "...", "review_after": null},
  "rationale": "Human-readable summary of why this level was assigned",
  "basis": ["deployment_claim_type=franchisee_deployment", "scope_unit_count=125", "estimated_franchise_groups=7"],
  "last_assessed": "2026-06-10"
}
```

`relationship_classification` is a **derived/advisory field** — a heuristic best estimate from available evidence, always paired with `confidence`. It is optional; relationships with insufficient evidence (`deployment_claim_type` of `logo_or_customer_page`, `reference_only`, or `unknown`) remain unclassified rather than guessed.

`level 6 (strategic_platform)` is never auto-assigned by the heuristic classifier — "mission-critical / hard to replace" requires manual analyst confirmation (typically `vendor_role=system_of_record_pos` plus `systemwide_deployment` plus corroborating strategic context).

Sticky manual classification: once a relationship has been manually classified (or assigned `level: 6`), the heuristic classifier (`relationship_classification.py classify-all`) will not overwrite it unless run with `--force` — mirroring the sticky-substantiated rule above.

New `deployment` fields supporting this model:
- `estimated_franchise_groups` — number of distinct operator/franchise groups known to have deployed the product.
- `geographic_concentration` — free text describing geographic spread, e.g. `"national"`, `"Southeast US"`, `"California pilot markets"`.
- `corporate_vs_franchise` — `corporate_mandated | corporate_recommended | franchise_choice | mixed | unknown`, describing whether the deployment originates from corporate direction or independent franchisee choice.

Compute/refresh via `python3 system/scripts/relationship_classification.py classify-all`. Query enrichment (`ecosystem_intelligence.py query-vendor` / `query-brand`) surfaces `relationship_classification` alongside `estimated_total_stores` and a `classification_breakdown`, so the headline metric for vendor footprint is deployed-store-count and penetration, not raw relationship/logo count.

---

### Watch List — `ecosystem_intelligence.json#watch_list[]`

An array embedded inside the ecosystem graph. Each entry:

```json
{
  "entity_id": "brand-mcdonald-s",
  "priority": "tier_1",
  "added_at": "2026-05-27",
  "added_by": "user | cos",
  "reason": "Primary target account",
  "last_signal_at": "2026-05-27",
  "interrupt_eligible": true
}
```

Tier semantics:
- `tier_1` — User-curated. Interrupt-eligible. Any qualifying signal triggers consideration before the next daily brief.
- `tier_2` — CoS-suggested based on active thread linkage or relationship coverage gaps. Standard brief inclusion.
- `tier_3` — Ambient. Included in broad ecosystem scan; no special handling or interrupt routing.

Only `tier_1` entries with `interrupt_eligible: true` populate the interrupt queue. Managed via `ecosystem_intelligence.py watch-list` or the `updateWatchList` API endpoint.

---

### Interrupt Queue — `inbox/ecosystem/interrupt_queue.jsonl`

One JSON object per line. Records are appended by `check-interrupt-queue` automation. Unacknowledged records are surfaced by `getEcosystemInterrupts` and must be rendered before the daily brief.

```json
{
  "interrupt_id": "intr-20260527-abc123",
  "generated_at": "2026-05-27T14:00:00Z",
  "entity_id": "brand-mcdonald-s",
  "entity_name": "McDonald's",
  "signal_class": "leadership_change | rfp_cycle_signal | extreme_pain | vendor_displacement",
  "signal_summary": "One-sentence description of the triggering signal.",
  "confidence": "high | medium",
  "source": "src-id-or-url",
  "watch_list_tier": "tier_1",
  "acknowledged": false,
  "acknowledged_at": null
}
```

Qualifying `signal_class` values for interrupt generation: `leadership_change`, `rfp_cycle_signal`, `extreme_pain`, `vendor_displacement`. Interrupt items expire after 48 hours of detection. Acknowledgment endpoint: `POST /graphs/ecosystem/interrupts/{interrupt_id}/acknowledge` (to be implemented — see D5 gap closure).

---

### Micro Graph Index — `graphs/micro/index.json`

```json
{
  "contract": "rb_micro_graph_presence_index_v1",
  "version": 1,
  "description": "Lightweight index of all registered RB micro ecosystem graphs.",
  "trust_hierarchy": [
    "user_artifact",
    "rb_micro_graph",
    "rb_macro_graph",
    "verified_external_source",
    "general_model_knowledge"
  ],
  "graphs": [
    {
      "graph_id": "micro_ecosystem:mcdonalds_us_ops",
      "graph_slug": "mcdonalds_us_ops",
      "graph_type": "micro_ecosystem",
      "available": true,
      "entity_name": "McDonald's",
      "entity_aliases": ["McDonalds", "MCD", "NSN", "StoreTech"],
      "scope_domains": ["US operations", "franchise structure", "store technology"],
      "question_domains": ["pos_system", "field_support", "co_op_structure"],
      "implicit_trigger_terms": ["nsn", "storetech", "nso"],
      "freshness_date": "2026-05-27",
      "confidence_label": "verified | inferred | estimated"
    }
  ],
  "generated_at": "2026-05-27T00:00:00Z"
}
```

Consult this index via `getMicroGraphIndex` before answering any company-specific factual question. If a matching graph exists, call `getMicroGraphSummary` with the appropriate query. Every micro-graph-backed answer must cite: source artifact name, `freshness_date`, and `confidence_label`.

---

### Passive Intelligence Evaluation Metadata

Output contract: `rb_passive_intelligence_evaluation_v1`. Produced by `passive_intelligence.py` / `evaluatePassiveIntelligence`. Per-claim fields:

| Field | Type | Description |
|---|---|---|
| `source_type` | string | `vendor_blog`, `news_article`, `social_post`, `analyst_report`, `user_upload`, `transcript`, `unknown` |
| `source_quality` | string | `high`, `medium`, `low`, `unknown` — derived from source model authority tier |
| `confidence_score` | float 0–1 | Composite score: base from source quality + corroboration bonus − incentive penalties |
| `corroboration_count` | int | Number of existing ecosystem records that match or support this claim |
| `corroboration_sources` | list[str] | IDs of corroborating ecosystem records |
| `claim_status` | string | `verified`, `likely_true`, `plausible_but_unverified`, `insufficient_evidence`, `disputed`, `false` |
| `verification_timestamp` | date | ISO date when evaluation was run |
| `narrative_classification` | string | `factual_report`, `vendor_claim`, `marketing_adjacent_positioning`, `strategic_weak_signal`, `analyst_assessment`, `social_signal`, `rumor`, `opinion`, `anecdotal_operator_feedback`, `thought_leadership_narrative`, `verified_reporting` |
| `graph_mutation_eligibility` | string | `canonical_fact`, `corroborated_intelligence`, `emerging_narrative`, `weak_signal`, `vendor_positioning`, `not_eligible` |
| `strategic_relevance_score` | float 0–1 | Relevance to Todd's active targets and domains |
| `corroboration_search_plan` | object | Source classes, search queries, promotion rule, and disqualifiers RB must use when external corroboration is still needed |
| `uncertainty_preserved` | bool | True when eligibility is not `canonical_fact` or `corroborated_intelligence` |

Evaluation summaries include `corroboration_search_required` and `corroboration_search_queue` when any claim needs outside validation. This queue is a retrieval plan, not corroboration. It must not increase confidence until a primary source or independent credible source is actually captured and attached.

**Promotion rules:**
- Only `graph_mutation_eligibility=canonical_fact` or `corroborated_intelligence` may update canonical relationship/entity truth layers.
- `emerging_narrative` and `weak_signal` remain as signals only.
- `vendor_positioning` and `not_eligible` must never mutate entity or relationship records.

**Daily brief rendering requirements for confidence/corroboration metadata:**
- Any signal or relationship sourced from passive intelligence must display `claim_status` and `confidence_score` when shown in the brief.
- If `corroboration_count >= 1`, show: `✓ corroborated by N source(s)`.
- If `graph_mutation_eligibility` is `vendor_positioning` or `weak_signal`, prefix the brief entry with: `[Unverified signal]`.
- Do not render `vendor_positioning` claims in the same visual tier as `substantiated` relationships.
- If `uncertainty_preserved=true`, the brief must not present the claim as established fact.

---

## Capture Source Permission Profile (`settings.json` → `capture_sources.sources[]`)

Each source entry may declare `content_access` and a `permission_profile` block.
Both optional; absence means the safe default (`metadata_only`, read toggles on,
content-persistence toggles off).

- `content_access` ∈ `metadata_only | on_demand | persistent`. Default `metadata_only`.
  - `metadata_only` — sender/recipient/timestamp/subject/folder/flags/attachment-metadata
    only, never body/attachment content. Default for every source, existing and future.
  - `on_demand` — content read transiently for one user-initiated request; never persisted
    beyond `ephemeral_raw` retention class.
  - `persistent` — content may be persisted; requires `source_permission.check_compliance_precondition()`
    (soft-checks an active `system/employers/<id>/profile.yaml`) plus explicit confirmation.
- `permission_profile` toggles: `relationship_graph`, `task_extraction`, `calendar_correlation`
  (derive-only, default `true`) vs. `automatic_body_reading`, `automatic_attachment_reading`,
  `long_term_content_storage`, `publish_outside_workspace` (content-touching, default `false`;
  any `true` here requires `content_access != "metadata_only"`).
- Enforced by `system/scripts/source_permission.py`, composed on top of the existing
  `privacy_guard.py` guard chain — not a new gate mechanism.

Example:

```json
{
  "id": "outlook_work",
  "enabled": false,
  "connector": "api",
  "content_access": "metadata_only",
  "permission_profile": {
    "relationship_graph": true,
    "task_extraction": true,
    "calendar_correlation": true,
    "automatic_body_reading": false,
    "automatic_attachment_reading": false,
    "long_term_content_storage": false,
    "publish_outside_workspace": false
  }
}
```

---

## `baseline_index.json`

A JSON array. One entry per contact. This is the system's universe of people.

```json
[
  {
    "id": "jane-doe",
    "name": "Jane Doe",
    "current_company": "Acme Inc",
    "current_role": "VP Engineering",
    "location": "San Francisco, CA",
    "linkedin_url": "https://linkedin.com/in/janedoe",
    "email": null,
    "phone": null,
    "sources": ["linkedin_export_2026-04-12"],
    "signal_class": "VC",
    "rc_state": null,
    "rc_tier": null,
    "last_touch": null,
    "circles": [],
    "tags": [],
    "notes": "",
    "relationship_health": {
      "computed_at": "2026-07-06T05:00:00+00:00",
      "drr_score": 62.4,
      "drr_components": {"recency": 0.8, "tier_weight": 0.85, "circles": 0.5, "completeness": 1.0, "evidence": 0.5},
      "communication_frequency_90d": 2.3,
      "outstanding_follow_ups_count": 1,
      "last_response_overdue_at": "2026-06-30T00:00:00+00:00",
      "last_awaiting_response_at": null,
      "source": "relationship_health_aggregate.py"
    }
  }
]
```

- `signal_class` ∈ `VC | NPR | LMI | LKI | RC`.
- `rc_state` is non-null only when `signal_class == "RC"`. Values: `ACTIVE | PARKED | CLOSED`.
- `rc_tier` is non-null only when `signal_class == "RC"`. Values: `inner | broader | dormant_valuable`.
- `last_touch` is the most recent IB date for this person.
- `circles` is an array of Circle IDs this person belongs to.
- **Source-specific fields** may be added with the source as a prefix (e.g. `linkedin_connected_on`). These carry input metadata and don't change the core schema. Currently used: `linkedin_connected_on` (raw string from LinkedIn export, e.g. `"29 Apr 2025"`).
- **`relationship_health`** (optional; written only by `relationship_health_aggregate.py`, never hand-edited). Fully metadata-derived — never contains message/email body content or snippets. Recomputed on each pipeline run; `computed_at` marks freshness. `drr_score`/`drr_components` mirror the output of `rb_core.drr_score()` (components are the raw 0-1 breakdown; the top-level `drr_score` is the final 0-100 score). `outstanding_follow_ups_count`, `last_response_overdue_at`, `last_awaiting_response_at` are derived from `relationship_signals.py`'s existing `response_overdue`/`awaiting_response` signal emission (`email_overlay.sent_followups`) — this field persists what `relationship_signals.py` already computes per run, it does not introduce new computation. Unrelated to the vendor-deployment-strength `relationships[].relationship_classification` field and to the Employment Governance Layer's proposed `compliance_category` field — do not conflate the three.
- **`tags`** is multi-valued. The vocabulary in current use:
    - **Cohort tags**: `par-alumni`, `mcd-franchise`, `otp-level-3`, `otp3-leaders`, `nomadgo-alumni`, `success-champions`, `hospitality-table`, `author-faith`, `non_linkedin_contact`.
    - **Functional/categorization tags** (per Tenets, Feature F28): `starting-lineup`, `all-star`, `bench`, `observe`, `dormant`, `strategic-advisor`, `connector`, `opportunity-relationship`, `referral-partner`. Multi-valued — a contact can be `starting-lineup` + `connector` + `referral-partner`.
    - **Source/lineage tags**: `salvage_added`, `linkedin_disconnected_<date>`, `rc_candidate_<date>` (transient).
- **`notes` compaction convention.** The `notes` field starts as a single current-state paragraph. Dated audit-trail breadcrumbs go in a `## History` section appended below the current-state paragraph, one per line, oldest first. When a session adds a new breadcrumb, the current-state paragraph is refreshed to reflect the new fact and the breadcrumb is appended to History — never just appended on top of an old summary. This keeps the top of the notes field digestible while preserving full audit trail. Cards (`cards/<id>.md`) follow the same convention if `notes` content exists there.

---

## Relationship Card — `cards/<id>.md`

One file per RC. Created when you confirm RC promotion. Mirrors and extends the baseline entry.

An RC is **not** a contact record. It is the system's stored *relationship meaning* — the narrative arc, trust state with momentum, leverage (the asymmetric value running in each direction), what's lingering from prior contact, unresolved movement, and known risks. These are first-class fields, not optional.

```markdown
---
id: jane-doe
name: Jane Doe
state: ACTIVE
tier: inner
trust_state: stable
momentum: positive
created: 2026-04-08
last_touch: 2026-04-08
linkedin_url: https://linkedin.com/in/janedoe
current_company: Acme Inc
current_role: VP Engineering
circles: [success-champions, target-employers]
---

# Jane Doe

## Why this matters
She's been a sounding board on every consulting pivot since 2022. High-trust, fast responder, generous with intros.

## Narrative arc
Met March 2022 at a Success Champions monthly meetup. Got close after co-presenting at the SC summit in 2023. Through a rough 2024 patch when she was navigating a difficult co-founder split, I checked in monthly without asking for anything — that's when this became real. Trajectory: deepening, mutual.

## Trust state
Stable, high. Last 12 months: positive momentum. She defaulted to including me in her advisory circle for the Acme move; I've delivered on every intro I've offered.

## Leverage (asymmetric value)
- *What she uniquely gives me*: candid GTM judgment for B2B-into-restaurants deals; intros to operator-CEOs in her angel portfolio.
- *What I uniquely give her*: outside-in strategy gut-checks; intros into the Success Champions hospitality cohort.

## What's lingering
*Anything left unsaid; warmth or awkwardness from the last interaction; vibe to remember.*
- April coffee ended with her asking about my Q3 roadmap and me deflecting. Note to self: come back to that with a real answer.

## Unresolved movement
*Pending dynamics — including soft ones that haven't become formal loops yet.*
- She hinted in March that she wants to host a hospitality-tech operator dinner this fall; I haven't followed up.

## Risks
*Known problematic dynamics. Distinct from "What's lingering" (texture) and "Unresolved movement" (open dynamics) — Risks is what's actively in the way.*
- (none currently flagged)

## Their world
- Currently leading a 40-person eng org at Acme.
- Hiring 4 senior PMs in Q3.
- Personally interested in ops-tech for restaurants (small angel portfolio in this space).

## How to engage
- Direct, no preamble. Hates LinkedIn DMs; prefers SMS.
- Best with concrete asks ("would you intro me to X for Y") not open-ended ones.

## Recent IBs
- 2026-04-08 — Coffee at SF office. Discussed Q3 hiring plans.
- 2026-03-15 — LinkedIn message exchange about her new role.

## Open loops
- Send Jane intro to Sam re: Senior PM role (target close: 2026-04-15)
```

Field semantics:

- `trust_state` ∈ `rising | stable | strained | drifting | broken`. The current quality of trust. Updated when an IB materially changes it.
- `momentum` ∈ `positive | flat | negative`. The direction of travel over the last few interactions. Distinct from `trust_state`: a relationship can be *stable / negative* (high trust, drifting away) or *strained / positive* (recovering).
- **Narrative arc** is prose, not bullets. Where this relationship came from, key inflection points, where it's heading.
- **Leverage** is the explicit asymmetry of value running in each direction. Be specific. Generic leverage ("she's a great connector") doesn't earn its place; concrete leverage ("she has the trust of three operator-CEOs in restaurant tech you can't otherwise reach") does.
- **What's lingering** captures emotional residue and unsaid material — distinct from open loops, which are concrete commitments.
- **Unresolved movement** captures soft pending dynamics — hinted opportunities, half-said things, vibes that may become loops but haven't yet. The system reads this when generating `today.md` to surface things you'd otherwise lose.
- **Risks** captures known problematic dynamics — someone planning to move firms, declining health, an unresolved tension, a competitor relationship that constrains what can be shared. Distinct from `What's lingering` (texture) and `Unresolved movement` (open soft dynamics). Risks is what's *actively in the way*.

---

## Interaction Brief — `briefs/<date>-<id>.md`

Append-only. Created once per qualifying interaction. Never edited after creation.

```markdown
---
id: 2026-04-08-jane-doe
person: jane-doe
date: 2026-04-08
channel: in_person
direction: bidirectional
substance: high
duration_min: 45
artifact: null
---

## Summary
Coffee at SF office. ~45 min. Caught up on Q1; Jane shared Acme is hiring 4 senior PMs and asked if I knew anyone.

## Signal implication
LKI — bidirectional, substantive. Jane's prior class was LMI; promote to LKI and queue RC review.

## Loops opened
- Send Jane intro to Sam re: Senior PM role (closure target: 2026-04-15)

## Loops closed
- (none)
```

- `channel` ∈ `in_person | call | video | email | sms | linkedin | other`.
- `direction` ∈ `inbound | outbound | bidirectional`.
- `substance` ∈ `low | medium | high`. (Low = "happy birthday." Medium = a real exchange. High = substantive working content.)
- `artifact` is a path to a transcript, notes file, or thread export, or `null`.

---

## Circle — `circles/<id>.md`

Goal-anchored network activation. **Every Circle has a `circle_type` that governs how the intro engine treats it** — see field semantics below.

### Person-based Circle

```markdown
---
id: tech-sales-cs-leaders
name: Tech Sales and Customer Success leaders to know
member_type: person
circle_type: affinity_circle
goal: Build durable peer network of sales/CS leaders in B2B tech for trade-craft, references, and intro reciprocity.
status: forming
created: 2026-05-07
target_size: 50
---

## Member-fit criteria
- VP+ in Sales or Customer Success at a B2B tech company ($10M+ ARR).
- Active in at least one of: Pavilion, RevGenius, Success Champions Networking.
- Reciprocity orientation (gives intros, not just asks).

## Current members (RCs only)
- (none yet)

## Candidate members (LMI/LKI signal in baseline)
- (auto-populated as baseline grows)

## Next moves
- Seed list from Success Champions roster.
- Identify top 5 in current baseline who already qualify.
```

### Account-based Circle

```markdown
---
id: target-employers
name: Companies that might employ me or my consultancy
member_type: account
circle_type: affinity_circle
goal: Land next consulting engagement or full-time role.
status: forming
created: 2026-05-07
target_size: 30
---

## Account-fit criteria
- Restaurant/hospitality tech, or restaurant/hospitality groups with internal tech functions.
- Stage: Series B through public, or established multi-location operators.
- Geography: open, U.S. preference.

## Target accounts
| Company | Why fit | Best contact in network | Contact's signal class | Status |
|---|---|---|---|---|
| (to be populated) | | | | |

## Next moves
- Add 10 starting accounts with fit notes.
- For each, identify the best inroad from baseline.
```

- `status` ∈ `forming | activating | active | dormant | closed`.
- For `account` Circles, `target_accounts` table replaces the person-based members list. People-at-accounts surface via `baseline_index.json` filtered by `current_company`.
- **`circle_type`** ∈ `community_chapter | affinity_circle`. Determines how the intro engine treats Same-Circle membership.
  - **`community_chapter`** — an active social structure where members already know each other through recurring participation. Examples: the Hospitality Table SCN chapter (`hospitality-table`), the OTP3 leadership cohort (`otp3-leaders`), the former PAR alumni group (`former-par-employees`), SCN broadly (`success-champions`). Two people sharing a `community_chapter` are **probably already acquainted**; the intro engine should treat Same-Circle here as a **suppression signal** unless evidence shows the two members haven't actually met.
  - **`affinity_circle`** — a curated semantic grouping of people who share domain or target criteria but may never have crossed paths. Examples: `tech-sales-cs-leaders`, `target-restaurants-tech-leaders`, `target-employers`. Two people sharing an `affinity_circle` are **catalog-mates, not acquaintances**; the intro engine should treat Same-Circle here as a **positive affinity signal** and *prefer* such intros (per the original ARCHITECTURE.md "same-Circle is exactly the reason" rule).

---

## `intro_brokers.md`

Derived. Regenerated, not hand-edited (your edits will be preserved in a `## Notes` section per broker, but the table above is regenerated).

```markdown
# Intro Brokers

Top connectors in your network, by domain. A broker scores high when they (a) hold an RC with you, (b) appear in many other people's networks across a domain, and (c) have a track record of giving introductions.

## Hospitality / restaurant tech
| Broker | RC tier | Domain depth | Notes |
|---|---|---|---|
| (to be populated) | | | |

## B2B SaaS sales leadership
| Broker | RC tier | Domain depth | Notes |
|---|---|---|---|
| (to be populated) | | | |

## Notes
(Free-form per-broker notes here, preserved across regenerations.)
```

---

## `loop_ledger.md`

Open loops awaiting deliberate closure.

```markdown
# Loop Ledger

| ID | Opened | Person/Company | Loop | Closure target | Status |
|---|---|---|---|---|---|
| L-2026-04-08-001 | 2026-04-08 | Jane Doe | Send intro to Sam re: Senior PM role | 2026-04-15 | open |
```

- `Status` ∈ `open | closed | abandoned`. Abandoned requires a one-line reason in a `## Closed/abandoned` section below the table.

---

## `today.md`

Regenerated daily. Sections appear only when they have content. If nothing important is happening, the file says so.

```markdown
# Today — 2026-05-07

## Crossings
- (none)

## Loops past closure target
- (none)

## RC promotions queued for your confirmation
- (none)

## Today's meetings (context briefs)
- (none)

## Circle moves
- (none)

## On-deck intro brokers (when you've named a target)
- (none)

---

If nothing here looks meaningful: nothing here is meaningful. Tomorrow's brief regenerates from real signals only.

## Competitive Vulnerability & RFP Early Warning — `competitive_vulnerability.json`

Generated by `system/scripts/competitive_vulnerability.py --cache` (and
wrapped via `rb_core.write_cache`, so the file on disk is the standard
`{_generated_at, _source, _baseline_mtime, data}` envelope around the
`data` shape below). Scores each `entity_alerts.MANDATORY_RESTAURANT_BRANDS`
account on its likelihood of evaluating/replacing a technology platform
(POS, Payments, Loyalty, Back Office, Inventory, Digital Menu Board, Labor,
AI Platform) in the next 6-18 months — *before* an RFP is issued.

Inputs (all best-effort, missing files never raise):
- `entity_alerts_cache.json[entity].vulnerability_items` — exec-change,
  tech-hiring, strategic-change, and RFP/RFI items classified by
  `vulnerability_taxonomy.classify_vulnerability` (kept up to 540 days,
  see `entity_alerts.VULNERABILITY_ITEM_TTL_DAYS`).
- `web_scanner_cache.json` items entity-tagged to the brand, reclassified
  via the same taxonomy.
- `linkedin.daily_signals.jsonl` entries whose `entities.companies` match
  the brand and whose `topics` match `vulnerability_taxonomy.OPERATIONAL_FRICTION_TOPICS`.
- `ecosystem_intelligence.json` `uses_vendor_for_category` relationships —
  vendor financial-distress signals (layoffs, restructuring, missed
  earnings) are attributed back to the brand, tagged with the vendor's
  `category`.
- `signal_correlation.json` momentum — boosts `confidence` when a brand
  also shows cross-source momentum.

```json
{
  "_generated_at": "2026-06-10T12:00:00+00:00",
  "window_days": 540,
  "tier_thresholds": { "high_risk": 70, "emerging": 45, "watch": 25 },
  "brands": [
    {
      "entity": "Brand A",
      "vulnerability_score": 67,
      "tier": "emerging",
      "confidence": "high",
      "estimated_horizon_months": 6,
      "rfp_detected": false,
      "potential_categories": ["pos", "payments"],
      "category_contributions": { "exec_change_tech": 30, "tech_hiring": 25, "financial_distress": 12 },
      "signals": [
        {
          "category": "exec_change_tech",
          "category_label": "Tech Executive Change",
          "title": "Brand A appoints new CIO to modernize POS platform",
          "url": "https://...",
          "pub_date": "2026-05-11",
          "signal_badge": "[LEADERSHIP]",
          "source": "google_news_search",
          "weight_contribution": 30.0
        }
      ],
      "why_it_matters": "Tech Executive Change: Brand A appoints new CIO to modernize POS platform; Technology Hiring Signal: Brand A hiring Director of Restaurant Technology..."
    }
  ]
}
```

- `brands` is sorted by `vulnerability_score` descending and only includes
  brands scoring `>= tier_thresholds.watch` (25) **or** with `rfp_detected: true`
  (an RFP-only signal can surface a brand at score 0, tier `watch`).
- `tier`: `high_risk` (score >= 70), `emerging` (45-69), `watch` (25-44, or
  RFP-only).
- `confidence`: `high` (>=3 distinct categories triggered, or 2 + momentum),
  `medium` (2 categories, or 1 + momentum), else `low`.
- `estimated_horizon_months`: 3/6/12/18 — the most urgent
  `category_horizon()` among categories contributing >=50% of their cap;
  forced to 3 if `rfp_detected`.
- Each signal's `weight_contribution` = `category_weight * recency_decay`
  (1.0 if <=90 days old, 0.6 if <=270, 0.3 if <=540, excluded beyond 540),
  and per-category contributions are capped at that category's weight
  before summing into `vulnerability_score` (clamped to 100).

### `daily_brief.py` report key — `competitive_vulnerability`

`daily_brief.py` imports `competitive_vulnerability` best-effort
(`_HAS_COMPETITIVE_VULNERABILITY`) and adds the report above verbatim under
`report["competitive_vulnerability"]` (empty `{"brands": []}`-shaped dict on
import/build failure). A `rendering_rules` entry instructs the Custom GPT to
render this as the "Competitive Opportunity Watchlist" section, placed after
Watchlist Intelligence, grouped into "High-Risk Opportunities" / "Emerging
Opportunities" / "Watch List" by tier, with an "RFP/RFI detected" callout
when `rfp_detected` is true.
```

---

## Account Dossier stakeholder aliases — `artifacts/data/<dossier>.json#key_contacts[].aliases`

Account dossiers (`_schema: rb_account_dossier_v1`, registered in
`artifacts/registry.json` as `artifact_type: account_dossier`) are the
entity-resolution layer for active opportunities. Each entry in
`key_contacts[]` may carry an `aliases` map so inbound communications can be
matched back to a known stakeholder regardless of which email address or
phone number they use:

```json
{
  "contact_id": "christian-jackson-gpn",
  "name": "Christian Jackson",
  "title": "Senior Recruiter",
  "signal_class": "VC",
  "relationship": "...",
  "last_touch": "2026-06-09",
  "loop_ids": [],
  "aliases": {
    "emails": ["cj77746@globalpayments.com", "christian.jackson@e-hps.com"],
    "phones": []
  }
}
```

- `aliases.emails` and `aliases.phones` are lowercased/digit-normalized at
  read time by `opportunity_signal_correlation.py`. A contact's top-level
  `email` field (if present, e.g. `foods_connected.json`'s key_contacts) is
  also folded in automatically.
- A dossier whose `status` is `active` or `building` in `artifacts/registry.json`
  is in scope for stakeholder-signal scanning. Contacts with no aliases are
  ignored (nothing to match against).
- Dossiers may also carry `opportunity_state` (e.g. `role_exploration`,
  `decision_stage`) and `opportunity_state_history` — an append-only list of
  `{state, as_of, evidence}` recording why the state changed and citing the
  source message(s).

### `system/.cache/opportunity_signals.json` — cross-channel opportunity stakeholder signals

Produced by `opportunity_signal_correlation.py` (best-effort import in
`daily_brief.py` as `_osc`, report key `opportunity_signals`). Answers "did a
known stakeholder on an active opportunity just say something that changes
the opportunity's state?" — distinct from `job_intelligence.py`, which
fit-scores email/LinkedIn for *new* job postings and is gated by
`job_search_active`. This module is ungated (it only acts on opportunities
already tracked via an account dossier) and scans **email and SMS**.

```json
{
  "generated_at": "2026-06-10T12:21:32+00:00",
  "scan_window_days": 14,
  "correlation_window_hours": 48,
  "dossiers_scanned": 2,
  "opportunity_count": 1,
  "opportunities": [
    {
      "artifact_id": "account_dossier:global_payments",
      "entity": "Global Payments Inc.",
      "data_path": "system/artifacts/data/global_payments.json",
      "opportunity_state": "decision_stage",
      "top_urgency": "critical",
      "cross_channel_correlated": true,
      "signal_count": 3,
      "signals": [
        {
          "channel": "email",
          "thread_id": "19eaebc325e420ee",
          "at": "Tue, 9 Jun 2026 23:33:44 +0000",
          "stakeholder_contact_id": "christian-jackson-gpn",
          "stakeholder_name": "Christian Jackson",
          "milestones": [
            {"milestone_type": "final_feedback", "urgency": "critical", "matched_text": "final feedback"},
            {"milestone_type": "scheduling_request", "urgency": "high", "matched_text": "time for a quick call"}
          ],
          "urgency": "critical",
          "cross_channel_correlated": true,
          "correlated_with": [{"channel": "sms", "stakeholder_name": "Ryan Hildebrand", "at": "...", "snippet": "..."}]
        }
      ],
      "recommended_action": "Interview process appears complete. Be available for the recruiter's call and prepare for an offer / final-decision conversation."
    }
  ]
}
```

- `MILESTONE_PATTERNS` classify message *meaning* (final feedback,
  decision/offer, reference/background check, onboarding, scheduling
  request, internal coordination) — `urgency` ∈ `critical | high | medium | low`.
- Two signals correlate (`cross_channel_correlated: true`) when they're on
  the same opportunity, from different channels and/or stakeholders, within
  `correlation_window_hours` (48h) of each other.
- `daily_brief.py` renders one item per opportunity under
  `sections.opportunity_signals`. Items with `top_urgency == "critical"` get
  `disposition: act_today` and are prepended to `morning_command_center` —
  ahead of the rest of the operating board — since these represent
  time-sensitive opportunity-state changes, not routine task work.
```

---

## Employer Profile & Policy — `system/employers/<employer_id>/`

Employment Governance Layer (EGL) Phase 1 — see `RB_EMPLOYMENT_GOVERNANCE_LAYER.md` for full design. One directory per employer/engagement; policies are hand- or LLM-assisted extractions of real source documents, never fabricated.

`profile.yaml`:

```yaml
employer_id: global-payments
relationship_type: primary_employer   # primary_employer | advisory_engagement
company: "Global Payments Inc. / Genius"
role: { title, responsibilities, products: [], customers: [], industry }
start_date: "2026-07-06"
end_date: null
status: active   # active | archived
manager: null
travel_expectations: null
compliance_requirements: []
security_classification: null
internal_systems: []
policy_refs: []   # policy_id list, resolved from policies/*.yaml
```

**Invariant:** exactly one profile has `status: active` AND `relationship_type: primary_employer` at a time. `relationship_type: advisory_engagement` profiles may coexist with the active primary employer (e.g. `bridgepoint-ops` alongside `global-payments`) without violating this. Checked by `compliance_engine.validate_employer_registry()`.

`policies/<policy_id>.yaml` — one file per source document, same shape regardless of policy type:

```yaml
policy_id: social-media-policy
title: "..."
source_document: "..."          # filename in source_docs/ (git-ignored)
version: "..."
effective_date: "YYYY-MM-DD" | null
superseded_by: null | policy_id
extracted_by: manual | assisted  # assisted = LLM-drafted from real source text, pending Todd's line-by-line review
last_reviewed: "YYYY-MM-DD"
permissions: ["..."]
restrictions: [{id, text}]
required_processes: [{trigger, process}]
escalation_rules: [{condition, action}]
```

`extracted_by: assisted` policies are not treated as legally authoritative — see `RB_EMPLOYMENT_GOVERNANCE_LAYER.md` Section 6 on why automated extraction is advisory only. `superseded_by` policies are kept on disk for historical reference but excluded from `compliance_engine.load_policies()`.

Source documents live in `system/employers/<employer_id>/source_docs/` and are git-ignored — they contain employer-confidential text; only the extracted YAML is committed.

Compliance checks (`system/scripts/compliance_engine.py`) evaluate outbound content against `restrictions[]` via a separate `DETECTION_RULES` keyword/phrase table (kept in code, not in the policy YAML, so detection heuristics can be tuned without touching the ground-truth extraction). Verdict: `overall_risk` (low/medium/high) → `recommendation` (allow/warn/block). Wired into `privacy_guard.gate_action()` for `CONTENT_BEARING_ACTION_TYPES` (`send_email`, `send_linkedin_message`) via optional `content`/`content_type` kwargs — a `block` verdict is unconditional and cannot be overridden by `confirmed=True`.

---

## Conference Campaign Intelligence Engine — `system/campaigns/<campaign_id>/`

Config-driven ABM-style engine (`system/scripts/campaign_engine.py`) turning a conference/user-conference/webinar into a structured campaign over the operator's **own personal network already in `baseline_index.json`** — never an employer CRM/customer export. Event type is a config setting; a new conference is a new `config.yaml`, not new code.

**Hard runtime guard:** the engine refuses to run unless `config.data_scope.source == "personal_network_only"`. This is enforced in code (`_assert_personal_network_scope()`), not left as documentation, because of the open AI-governance flag on employer-confidential data at `system/employers/global-payments/policies/code-of-conduct.yaml:79-87`.

`system/campaigns/registry.yaml` — index of campaign ids:

```yaml
version: 1
campaigns:
  - id: genius-user-conference-2026
    name: Global Payments Genius User Conference 2026
    status: active
    created: 2026-07-10
```

`config.yaml` — hand-authored campaign definition:

```yaml
campaign_id: genius-user-conference-2026
name: Global Payments Genius User Conference 2026
data_scope:
  source: personal_network_only     # hard-enforced; engine refuses to run otherwise
eligibility:
  known_registered_contact_ids: []  # seed list for people already known-registered before this campaign existed
  include_if_any:
    - tag_in: [starting-lineup, all-star, opportunity-relationship, referral-partner]
    - signal_class_in: [LKI, RC]
    - current_role_matches: ["VP", "SVP", "EVP", "Chief", "Head of", "Owner", "President", "Founder"]
  exclude_if_any:
    - current_company_in: []        # user-curated competitor list
      reason: competitor
      mode: exclude                 # exclude | penalize
    - tag_in: [consultant, vendor-conflict]
      reason: consultant_or_vendor
      mode: exclude
    - current_company_equals: "Global Payments Inc."
      reason: employee_of_hosting_company
      mode: exclude
    - already_registered: true
      reason: already_registered
      mode: exclude
scoring:
  weights:
    enterprise_brand: 30
    current_opportunity: 25
    existing_relationship: 20
    executive_decision_maker: 20
    technology_operations_leadership: 15
    current_customer: 0             # inert by default — see notes below
  enterprise_brand_list: []         # user-curated named list
  penalties:
    competitor: -100
tiering:
  mode: score_bands                 # score_bands | rule_based
  bands:
    - {id: tier_1_must_invite, label: "Tier 1 — Must Invite", min_score: 60}
    - {id: tier_2_should_invite, label: "Tier 2 — Should Invite", min_score: 35}
    - {id: tier_3_consider, label: "Tier 3 — Consider", min_score: 15}
```

- `eligibility.include_if_any` — OR logic; a baseline entry needs at least one hit to become a candidate. Empty means every baseline entry is a candidate (excludes still apply).
- `eligibility.exclude_if_any` — first matching rule wins. `current_company_in`/`current_company_equals` filter on the **prospect's own** `current_company` (e.g. "is this person of mine a competitor of the hosting company," or "is this one of my own colleagues at the hosting company") — never a lookup against any employer CRM/customer system. `mode: exclude` drops the entry from the roster entirely; `mode: penalize` keeps it visible with a large negative score modifier and a recorded reason, for audit.
- `scoring.weights.current_customer` — no signal exists in `baseline_index.json` for this today. Ships inert (`0`) by default; if enabled, the only compliant source is a manual tag the operator applies from personal knowledge (`gp-customer-contact-personal-knowledge`) — never an employer export.
- `executive_decision_maker` / `technology_operations_leadership` — heuristic keyword match over `current_role` (default keyword lists in `campaign_engine.py`, overridable via `scoring.executive_keywords`/`scoring.tech_ops_keywords`). A manual override tag (`exec-decision-maker` / `tech-ops-leader`) always wins over a heuristic miss or hit.
- `tiering.mode: rule_based` is an escape hatch for categorical tiers (e.g. restaurant-brand-size tiers) that aren't purely score-driven — same rule grammar as eligibility, first match wins.

`roster.json` — authoritative per-contact state (never mutates `baseline_index.json`):

```json
{
  "campaign_id": "genius-user-conference-2026",
  "generated_at": "2026-07-10T00:00:00+00:00",
  "prospects": [
    {
      "contact_id": "jane-doe",
      "name": "Jane Doe",
      "current_company": "Acme Restaurants",
      "current_role": "VP Operations",
      "score": 65,
      "score_breakdown": [
        {"dimension": "executive_decision_maker", "points": 20, "source": "current_role keyword match"},
        {"dimension": "existing_relationship", "points": 15, "source": "relationship_health.drr_score"}
      ],
      "tier": "tier_1_must_invite",
      "tier_label": "Tier 1 — Must Invite",
      "status": "not_yet_invited",
      "status_history": [{"status": "not_yet_invited", "at": "2026-07-10", "reason": "roster_created"}]
    }
  ],
  "excluded": [
    {"contact_id": "john-competitor", "name": "John Rival", "current_company": "Rival Payments Co", "reason": "competitor"}
  ]
}
```

- `status` ∈ `not_yet_invited | invited | registered | declined | attended | no_show | follow_up_due | follow_up_complete`. Once a contact reaches `registered`, `declined`, `attended`, or `no_show` (a "settled" status), subsequent `--build-roster` runs carry that entry forward unchanged rather than re-running eligibility/scoring — eligibility governs who *joins* the roster, not who stays on it.
- Status transitions are appended to `system/ri_events/<YYYY-MM>.jsonl` as `source.type: campaign_lifecycle` events (`signal.type: campaign_status_change`) via `ri_events.append()` — the relationship-learning record of what happened, not a second event log.

`company_rollup.json` — company-level aggregation derived from `roster.json`, not a reuse of the account-based Circle `target_accounts` table (Circles are durable goal-anchored constructs with their own lifecycle; a time-boxed campaign forced into that schema would drift the generic table with campaign-only columns):

```json
{
  "campaign_id": "genius-user-conference-2026",
  "generated_at": "2026-07-10T00:00:00+00:00",
  "companies": [
    {
      "company": "Acme Restaurants",
      "contact_count": 4,
      "top_tier": "tier_1_must_invite",
      "top_tier_label": "Tier 1 — Must Invite",
      "best_contact_id": "jane-doe",
      "best_contact_name": "Jane Doe",
      "best_contact_score": 65,
      "status_summary": {"not_yet_invited": 3, "invited": 1},
      "contact_ids": ["jane-doe", "..."]
    }
  ]
}
```

`roster_report.md` / `company_dashboard.md` — rendered markdown views of the above, regenerated on every run (`--report` regenerates without recomputing scores).

---

## Technology Lifecycle & Change Events — `system/technology_lifecycle/`

**Phase 0 (2026-10-01, revised 2026-10-01 after Deep Research Cycles 7–8): schema documented here, scaffolding files created, no enforcing code yet.** This is a Tier 1 (public, shared) intelligence layer per `system/DATA_TIER_ARCHITECTURE.md` — built from public sources, never Todd's private account judgment — that sits alongside `ecosystem_intelligence.json` rather than inside any account's Tier 2 tree. See `system/technology_lifecycle/README.md` and `RESEARCH_KICKOFF.md` for the operational/workflow side; this section is the field-level record shape.

Research is produced by Hunter using the `technology_replacement_lifecycle`
playbook and `system/scripts/hunter_cycle.py`. The authoritative artifact is a
validated Hunter JSON packet with an `rb.technology_lifecycle_research.v1`
payload. `system/templates/deep_research_technology_lifecycle_drop.md` and the
intelligence-drop inbox remain compatibility views/transport for downstream
consumers, not a separate research process.

### The core model: a Technology Relationship, not Brand → Vendor → Product

Cycles 7–8 established that `Brand → Vendor → Product` collapses ten distinct, separately-evidenced facts into one, and that collapse is where false conclusions creep in (an enterprise vendor *selection* is not evidence of *deployment*; a parent company's technology choice does not propagate to its subsidiary brands; one operator's penetration is not the brand's penetration). The schema's primary unit is therefore a **Technology Relationship**, identified by a `relationship_key`:

```json
{
  "parent_entity_id": "parent-<slug> or null",
  "brand_entity_id": "brand-<slug>",
  "operator_entity_id": "operator-<slug> or null -- a specific franchisee/operator organization, when the evidence is operator-level rather than brand-wide",
  "technology_category": "pos | pos_hardware | payments | back_office | inventory | labor_workforce | kds_kitchen_ops | loyalty | crm_cdp | mobile_apps | online_ordering | delivery_marketplace_orchestration | digital_menu_boards | drive_thru_ai | kiosks | voice_ai | computer_vision | automated_inventory | restaurant_ai_platform | ai_agents | predictive_operations",
  "vendor_entity_id": "vendor-<slug>",
  "product": "..."
}
```

A relationship moves through lifecycle states over time (next subsection), has layer-specific governance (`technology_governance.jsonl`), and — where evidence allows — a penetration time series (`technology_penetration.jsonl`). The entity hierarchy is `Parent → Brand → Franchisee/Operator → Location`; a technology observation may attach at any of these levels, and an observation at one level must never be silently propagated to another (a parent's technology choice does not imply its subsidiary brands use it; one franchisee's rollout does not imply the whole brand's). `entity_level` ∈ `parent | brand | operator | location` is required on every relationship-level record so a reader always knows which level the evidence actually supports.

The ten things that must stay separately represented (and the record/field that carries each): what was announced (`lifecycle_state: selected`/`announced_scope`), what was contracted (`contracted_scope`), what the franchisor requires (`technology_governance.jsonl`), what franchisees may choose (`technology_governance.jsonl`'s `governance_state`), what's actually deployed (`installed_scope`/`live_scope`), how much of the eligible estate is live (`technology_penetration.jsonl`), what competing products remain installed (`technology_penetration.jsonl`'s per-vendor rows for the same brand/category), how fast penetration is changing (`rollout_velocity`), why the change happened (`technology_change_events.jsonl`'s push/pull/economics fields), and whether it ultimately succeeded, stalled, reversed, or never scaled (`pilot_outcome`/`adoption_dimensions`/lifecycle state).

### The evidence-type vocabulary (applies to every factual field)

`evidence_type` ∈ `vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown`. A vendor claim is a discovery lead, never proof a claimed outcome occurred; independent/operator evidence is required before any field is treated as more than a lead. Firsthand RBB/operator intelligence (something Todd or a teammate directly knows) is supported too — tag it `evidence_type: operator_statement` or `independent_evidence` as appropriate with `source: "RBB firsthand"`, but it must never be silently presented as a public-source citation it isn't. See `system/technology_lifecycle/README.md` for full definitions and examples.

### `visibility_class` (added 2026-10-01, applied retroactively to every record type below)

Separate from `evidence_type` (how a fact was sourced) is `visibility_class` (who may see it): `public_shared | private_user | private_organization | restricted | unknown`. This technology-lifecycle layer is built from public-source ChatGPT research, so every record here defaults to `public_shared` — but the distinction matters because research *questions* are sometimes formulated from Todd's firsthand, private recollection (e.g. "Burger King historically had 10+ approved back-office vendors" was private operator knowledge before Cycle 8's research independently corroborated specific named vendors from public filings). The rule:

- A private observation that merely *motivated* a research question stays `private_user` and is never itself written into this public layer.
- Once independent public evidence corroborates the same fact, a **separate** `public_shared` record is created citing the public source — the private origin is not disclosed or required by that record, and the private record (wherever it lives) is never converted into the public one.
- `unknown` is the default when visibility can't be determined; an `unknown`-visibility record must not be exposed through Team Portal until resolved — never assume `public_shared` to avoid the extra step.
- This governs the whole `system/technology_lifecycle/` layer retroactively: every record created so far was built from `public_shared`-eligible research (ChatGPT Deep Research against public sources) and should carry `visibility_class: "public_shared"`; nothing already recorded here originated as a private observation written in directly.

This is the shared-infrastructure principle a separate, larger FDD Technology Governance & Economics research program (received 2026-10-01, not yet implemented — see `system/technology_lifecycle/FDD_GOVERNANCE_ECONOMICS_BRIEF.md`) will also depend on; it's introduced here first since this layer is live already.

### Lifecycle states

A technology relationship's state is event-sourced — every observed state is a new line in `technology_relationship_events.jsonl`, never an overwrite of the previous one (same append-only/supersedes discipline as the rest of this layer). `lifecycle_state` ∈:

`discovery | evaluation | rfi | rfp | pilot | selected | contracted | rollout_planned | rollout_active | rollout_paused | rollout_restarted | rollout_scaled | deployed | operationalized | expanded | renewed | displaced | retired | pilot_abandoned | pilot_not_scaled | technology_reversal | vendor_platform_discontinued | strategic_divestiture_with_continued_use | historical_deployment_current_state_uncertain`

`historical_deployment_current_state_uncertain` is the honest default for an old vendor announcement with no recent confirming evidence — see "Evidence decay" below. A relationship's current state is derived by taking its most recent `technology_relationship_events.jsonl` line, never assumed from the oldest announcement.

### Scope vocabulary — selection is not deployment

Never translate "Vendor X selected for 5,000 restaurants" into "Vendor X deployed at 5,000 restaurants." Each is a different, separately-evidenced scope field, every one a location count or a `{low, high}` range with its own `as_of` date, `confidence`, and `evidence_type` — never fabricated when only partial evidence exists:

`announced_scope | contracted_scope | mandated_scope | committed_scope | pilot_scope | installed_scope | live_scope | verified_scope`

### `technology_relationship_events.jsonl` — atomic lifecycle/scope ledger (primary ground truth)

Append-only (event-sourced, per `ARCHITECTURE.md`'s "event stream over snapshots" principle — a correction is a new line with `supersedes` pointing at the `event_id` it corrects, never an in-place edit). This is the fine-grained ledger every other technology-lifecycle view (current state, `technology_change_events.jsonl` narratives, Account Background Brief enrichment) derives from. First line is always a `_schema_header` record.

```json
{
  "event_id": "tre-burgerking-pos-par-2024-selected",
  "relationship_key": {
    "parent_entity_id": "parent-restaurant-brands-international",
    "brand_entity_id": "brand-burger-king",
    "operator_entity_id": null,
    "technology_category": "pos",
    "vendor_entity_id": "vendor-par-technology",
    "product": "PAR Brink POS"
  },
  "entity_level": "brand",
  "lifecycle_state": "selected",
  "state_date_or_range": "2024",
  "scope_observation": {
    "scope_type": "announced_scope",
    "locations_low": 7000, "locations_high": null,
    "unit_basis": "North American restaurants",
    "as_of": "2024-xx-xx"
  },
  "evidence": "...",
  "source_url": "...",
  "source_type": "press release | trade press | SEC filing | earnings call | investor presentation | franchisee communication | job posting | other",
  "confidence": "high | medium | low",
  "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown",
  "observed_at": "2026-10-01",
  "last_verified_current_date": "2026-10-01",
  "visibility_class": "public_shared",
  "supersedes": null
}
```

Notes:
- One line per distinct observation — a brand's POS relationship with a vendor will accumulate many lines over years (`selected` → `contracted` → `rollout_active` → a `scope_observation` with `scope_type: live_scope` every time a new penetration figure is independently verified → eventually `expanded`/`renewed`/`displaced`). This is intentionally more granular than `technology_change_events.jsonl` below.
- `operator_entity_id` is set only when the evidence is specific to one franchisee/operator org (e.g. "GPS Hospitality's ~400 Burger King restaurants on R365") — that observation rolls **up** into brand-level intelligence as evidence of partial penetration, and must never be presented as if it established the complete brand footprint (see the Burger King worked example below).
- `scope_observation` is optional on a pure state-transition line (e.g. a bare `rollout_paused` with no new count) and required whenever the line's purpose is to report a count/range.

### `technology_governance.jsonl` — governance by layer, FDD-sourced where available

A franchise system can have different governance per technology category simultaneously (mandated POS, approved-list back office, optional workforce management, pilot-only AI, etc.) — never assign one governance model to a whole brand. Append-only, same discipline.

```json
{
  "governance_id": "tg-burgerking-pos-2024",
  "brand_entity_id": "brand-burger-king",
  "technology_category": "pos",
  "governance_state": "mandated | mandated_category_brand_selected_vendor | approved_vendor_list | preferred_not_required | franchisee_choice_with_requirements | grandfathered | new_store_mandate | pilot_optional | corporate_only | unknown",
  "approved_vendors": ["vendor-par-technology"],
  "franchisor_change_authority": {
    "may_require_change_during_term": "yes | no | unknown",
    "expense_borne_by": "franchisor | franchisee | shared | unknown",
    "source": "FDD Item 11 citation or other",
    "note": "a contractual right to require a change is NOT evidence that a specific technology is currently mandated -- keep these separate"
  },
  "fdd_sourced_fields": {
    "fdd_year": "2025",
    "fdd_items_reviewed": ["5", "6", "7", "8", "11"],
    "required_technology": "...",
    "required_hardware": "...",
    "technology_fees": "...",
    "replacement_obligations": "...",
    "payment_requirements": "...",
    "loyalty_requirements": "...",
    "online_ordering_requirements": "...",
    "required_upgrades": "...",
    "differs_new_vs_existing_restaurants": "..."
  },
  "evidence": "...", "source_url": "...",
  "confidence": "high | medium | low",
  "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown",
  "observed_at": "2026-10-01",
  "last_verified_current_date": "2026-10-01",
  "visibility_class": "public_shared",
  "supersedes": null
}
```

FDDs (Franchise Disclosure Documents) are a priority source for this file — Items 5, 6, 7, 8, 11 in particular. A statement that the franchisor *may* require technology is not evidence that a particular technology is *currently* mandated; `franchisor_change_authority` and `fdd_sourced_fields.required_technology` are deliberately separate fields so that distinction survives into the record.

### `technology_penetration.jsonl` — time-series penetration, Brand × Category × Vendor × Product × Date

Append-only. Never fabricate a figure when only partial evidence exists — use `{low, high}` ranges. Distinguish location, operator, system-sales, transaction, and module penetration (the same vendor can show very different numbers on each); do not assume penetration in one module implies penetration in another module from the same platform vendor, and do not assume operator-level evidence establishes brand-wide penetration.

```json
{
  "observation_id": "tp-burgerking-backoffice-par-2026-01",
  "relationship_key": {
    "parent_entity_id": null, "brand_entity_id": "brand-burger-king",
    "operator_entity_id": null, "technology_category": "back_office",
    "vendor_entity_id": "vendor-par-technology", "product": "PAR Data Central"
  },
  "entity_level": "brand",
  "penetration_type": "location_penetration | operator_penetration | system_sales_penetration | transaction_penetration | module_penetration",
  "total_system_locations": null,
  "eligible_locations": null,
  "contracted_locations": null,
  "mandated_locations": null,
  "committed_locations": null,
  "pilot_locations": null,
  "installed_locations": null,
  "live_locations": null,
  "verified_locations": null,
  "estimated_locations_low": null,
  "estimated_locations_high": null,
  "penetration_pct_low": null,
  "penetration_pct_high": null,
  "remaining_opportunity": null,
  "rollout_velocity": "e.g. +400 locations/month, or null if unknown",
  "observation_date": "2026-01-15",
  "last_verified_date": "2026-01-15",
  "confidence": "high | medium | low",
  "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown",
  "source": "...",
  "visibility_class": "public_shared",
  "supersedes": null
}
```

An "enterprise standard" observation and an "installed base" observation are both valid and can coexist without conflict for the same brand/category — e.g. a vendor can be the selected enterprise standard while a different, older vendor is still the verified installed base at many locations. Record both; do not resolve the apparent tension by discarding one.

### `technology_change_events.jsonl` — reconstructed switch narrative

Append-only (event-sourced, per `ARCHITECTURE.md`'s "event stream over snapshots" principle — a correction is a new line with `supersedes` pointing at the `event_id` it corrects, never an in-place edit). First line is always a `_schema_header` record. Deliberately includes non-switch cases (renewed incumbents, abandoned pilots, canceled RFPs) alongside confirmed switches — anti-confirmation-bias is a design requirement, not an afterthought.

This is the **interpretive narrative layer** — why a change happened, what it cost, whether it worked — built on top of (and citing) the atomic observations in `technology_relationship_events.jsonl`. It is not the ground-truth ledger; when the two could conflict, `technology_relationship_events.jsonl`'s dated, sourced lines win and this record should be revised (a new line with `supersedes`).

```json
{
  "event_id": "tle-mcdonalds-pos-2019",
  "relationship_key_incumbent": {"brand_entity_id": "brand-mcdonalds", "operator_entity_id": null, "technology_category": "pos", "vendor_entity_id": "vendor-...", "product": "..."},
  "relationship_key_replacement": {"brand_entity_id": "brand-mcdonalds", "operator_entity_id": null, "technology_category": "pos", "vendor_entity_id": "vendor-...", "product": "..."},
  "brand_entity_id": "brand-mcdonalds",
  "technology_category": "pos",
  "unit_count_at_decision": 13500,
  "scope": "corporate | franchise | both | unknown",
  "previous_technology": {
    "vendor": "...", "product": "...",
    "deployment_date": "2014 to 2016",
    "deployment_date_confidence": "medium",
    "estimated_tenure_years": 5
  },
  "replacement_technology": {
    "vendor": "...", "product": "...",
    "decision_date": "...", "announcement_date": "...", "pilot_date": "...",
    "rollout_start_date": "...", "rollout_completion_date": "...",
    "deployment_scope": "pilot | regional | systemwide | franchisee-optional | unknown"
  },
  "push_factors": [
    {"factor": "...", "evidence": "...", "source_url": "...", "source_type": "...", "confidence": "high|medium|low", "primary": true, "evidence_type": "..."}
  ],
  "pull_factors": [
    {"factor": "...", "evidence": "...", "source_url": "...", "source_type": "...", "confidence": "high|medium|low", "primary": true, "evidence_type": "..."}
  ],
  "pre_change_signals": [
    {"t_offset_months": -12, "signal_type": "leadership_change | business_transformation | technology_signal | buying_signal", "description": "...", "evidence": "...", "source_url": "...", "confidence": "high|medium|low", "evidence_type": "..."}
  ],
  "pos_hardware_lifecycle": {
    "applicable": true,
    "hardware_manufacturer": "...", "hardware_model": "...",
    "operating_system": "Windows | Android | Linux | proprietary | unknown",
    "os_version": "...", "os_eol_date": "...", "hardware_eol_date": "...",
    "upgrade_in_place_possible": false,
    "physical_life_note": "...", "os_life_note": "...",
    "application_life_note": "...", "economic_refresh_life_note": "..."
  },
  "change_scope": "single_product | adjacent_bundle | platform_module_expansion | broad_stack_replacement",
  "existing_stack_retained": [
    {"technology_category": "loyalty", "vendor": "...", "product": "...", "note": "what stayed untouched when the replacement went in"}
  ],
  "phasing": [
    {"phase": "pilot | regional_franchisee_deployment | enterprise_rollout | subsequent_module_adoption", "date_or_range": "...", "scope_detail": "...", "evidence": "...", "source_url": "...", "evidence_type": "..."}
  ],
  "platform_relationship": {
    "vendor_also_sells": ["payments", "loyalty", "back_office"],
    "categories_not_purchased_from_same_vendor": ["loyalty"],
    "note": "e.g. operator picked Vendor X for POS but kept the incumbent for loyalty/payments/back office even though Vendor X offers those too -- the absence of consolidation is itself the recorded fact"
  },
  "follow_on_adoption": [
    {"technology_category": "payments", "vendor": "same vendor as replacement_technology.vendor", "adopted_date_or_range": "...", "months_after_initial_rollout": 18, "evidence": "...", "source_url": "...", "confidence": "high|medium|low", "evidence_type": "..."}
  ],
  "brand_maturity": "emerging_growth | established_regional | large_enterprise | unknown",
  "migration_burden": {
    "integrations_note": "...", "franchisee_coordination_note": "...",
    "hardware_replacement_note": "...", "training_note": "...",
    "data_migration_note": "...", "payment_certification_note": "...",
    "overall_burden": "low | medium | high | unknown"
  },
  "change_economics": [
    {"economic_factor": "contract_expiration | renewal_timing | price_increase | hardware_capex | implementation_expense | accelerated_depreciation | software_fees | technology_fees | training_expense | network_readiness_expense | payments_economics | processor_economics | vendor_subsidy | franchisor_subsidy | franchisee_contribution | legacy_support_expense | sunk_pilot_expense | opportunity_cost | organizational_support_intensity", "note": "...", "evidence": "...", "source_url": "...", "confidence": "high|medium|low", "evidence_type": "..."}
  ],
  "need_vs_willingness_vs_ability": {
    "need_to_change": "...", "willingness_to_change": "...",
    "ability_to_change": "...", "economic_justification_to_change": "...",
    "note": "need, willingness, ability, and economic justification are four separate questions -- a bad incumbent does not automatically create a sales opportunity, and an adequate incumbent can become vulnerable for reasons unrelated to dissatisfaction (contract expiration, price increase, hardware EOL, leadership change, acquisition, transformation initiative, franchisee pressure, expansion)"
  },
  "migration_capacity": {
    "factors_note": "free text covering whatever of: location count, number of franchise organizations, franchisee concentration, technology-layer criticality, legacy tenure, integration dependencies, hardware requirements, training burden, data migration, support resources, leadership bandwidth, concurrent transformation projects, rollout deadline, financial capacity, prior implementation experience is actually evidenced",
    "note": "do not reduce this to brand size -- a large system can move quickly with narrow scope and intensive support; a small system can move slowly under difficult governance or economics"
  },
  "prior_implementation_scar": {
    "has_scar": false,
    "description": "a previous failed or difficult project at this brand that plausibly affects THIS decision's pace/governance",
    "observed_effects": "e.g. stronger franchisee governance, longer pilots, additional testing, slower rollout, increased executive oversight, demand for references, preference for proven vendors, contractual protections, reluctance to change",
    "evidence": "...", "source_url": "...", "evidence_type": "..."
  },
  "adoption_dimensions": {
    "technical_performance": "positive | mixed | negative | not_evaluated | unknown",
    "employee_acceptance": "positive | mixed | negative | not_evaluated | unknown",
    "manager_acceptance": "positive | mixed | negative | not_evaluated | unknown",
    "guest_acceptance": "positive | mixed | negative | not_evaluated | unknown",
    "franchisee_acceptance": "positive | mixed | negative | not_evaluated | unknown",
    "economic_outcome": "positive | mixed | negative | not_evaluated | unknown",
    "operational_outcome": "positive | mixed | negative | not_evaluated | unknown",
    "note": "do not collapse these into one success/failure label -- technical success does not imply adoption success (e.g. a drive-thru voice AI can perform technically but be rejected on guest acceptance)"
  },
  "pilot_outcome": "expanded | extended | modified | paused | abandoned | not_scaled | replaced | no_public_evidence_of_scale | unknown | not_applicable",
  "opportunity_classification": "displacement_opportunity | whitespace_opportunity | platform_expansion_opportunity | incumbent_defense | rollout_completion_opportunity | unknown",
  "last_verified_current_date": "2026-10-01",
  "outcome_research": [
    {"horizon_months": 12, "state": "success_evidence_found | mixed_evidence | failure_or_abandonment_evidence | no_public_evidence_found", "evidence": "...", "source_url": "...", "evidence_type": "..."}
  ],
  "is_non_switch_case": false,
  "non_switch_note": null,
  "overall_confidence": "high | medium | low",
  "visibility_class": "public_shared",
  "supersedes": null,
  "recorded_at": "2026-10-05"
}
```

Notes:
- `brand_entity_id`/any `vendor_entity_id` referenced inside `previous_technology`/`replacement_technology` must resolve to an existing entity in `ecosystem_intelligence.json` — same "existing entities only" discipline as `team_tech_stack.py`. A brand not yet tracked there is a gap to surface, not a reason to invent a new id scheme.
- `outcome_research[].state` has no default — absence of negative evidence must never be coded as `success_evidence_found`. `no_public_evidence_found` is a first-class, expected outcome. `pilot_outcome: not_scaled`/`no_public_evidence_of_scale` is likewise not automatically a failure label — it's a distinct, honest state.
- Dates throughout are free-text ranges with a paired `*_confidence` field where provided, not forced ISO dates — never collapse an uncertain window into a fabricated exact date.
- `public: true` on every record — kept explicit rather than assumed, since this store exists specifically to be Tier 1.
- `change_scope` through `migration_burden` (added 2026-10-01) exist to test the two working hypotheses below, not to assume them — see that subsection. `existing_stack_retained` and `platform_relationship.categories_not_purchased_from_same_vendor` specifically record what a vendor *didn't* win even though it could have; that absence is itself the data point, not a null to skip.
- `follow_on_adoption` tracks whether the *same* vendor picked up an adjacent category 12–36+ months after the initial win — the record for the Platform Expansion Hypothesis below. Only record an entry here when there's real evidence of a separate, later adoption decision, not an assumption that it will happen.
- `change_economics` through `opportunity_classification` (added after Cycles 7–8) separate *why a change became possible* (economics, capacity, scars) from *whether it worked* (`adoption_dimensions`, `outcome_research`) and from *what it means commercially* (`opportunity_classification`) — these are three different questions and should not be collapsed into a single judgment.
- `last_verified_current_date` exists because historical deployment announcements go stale — see "Evidence decay" below. A `technology_change_events` record not reconfirmed in a long time should be treated as describing a historical, not necessarily current, state.

### Working hypotheses this dataset is designed to test

These are explicit hypotheses the lifecycle dataset should produce evidence for or against — not assumptions baked into how events get recorded. An event should be recorded accurately regardless of which way it cuts; the hypotheses explain *why* `change_scope`, `existing_stack_retained`, `platform_relationship`, `follow_on_adoption`, and `brand_maturity` exist as fields, not a conclusion to steer toward.

- **Stack Replacement Hypothesis.** Mature enterprise restaurant brands generally modernize incrementally — replacing individual layers or tightly related groups of systems over multiple buying cycles — rather than replacing the entire stack at once. Younger, rapidly growing, or emerging brands may be more likely to adopt a broader integrated platform at once, since they carry less legacy infrastructure, organizational complexity, and migration risk. `brand_maturity` + `change_scope` together are what let this be tested later; a single event doesn't confirm or refute it.
- **Platform Expansion Hypothesis.** In mature restaurant enterprises, a platform vendor is more likely to expand through sequential product adoption after establishing one successful initial deployment than through a single all-stack conversion. `follow_on_adoption` (did the same vendor pick up an adjacent category later?) and `platform_relationship` (did the operator decline to buy adjacent categories from that vendor at all?) are the evidence trail for this.
- Why this matters for Genius specifically: an incumbent competitor winning one layer (e.g. POS) doesn't necessarily mean the account is closed to Genius — it may instead indicate *which layer becomes contestable next and roughly when*, if the Platform Expansion Hypothesis holds for that vendor/segment. This is a reason to capture `follow_on_adoption` and `platform_relationship` carefully even when they're not the layer currently being researched.

### Evidence decay

Historical deployment announcements go stale, especially for AI, robotics, payments, and other fast-moving categories. Every relationship-level record (`technology_relationship_events.jsonl`, `technology_governance.jsonl`, `technology_penetration.jsonl`, `technology_change_events.jsonl`) carries `last_verified_current_date` separately from `observed_at`/`recorded_at`. A record whose `last_verified_current_date` is old relative to how fast that category moves should be read as *historically* deployed, not *currently verified* deployed — `lifecycle_state: historical_deployment_current_state_uncertain` is the explicit way to say this when no recent evidence exists at all, rather than silently letting an old record imply current truth.

### Burger King / RBI — canonical worked example

Used as the reference case for this entire model (not itself a populated data entry — any numbers here are illustrative of the *shape* of the intelligence, never real figures to copy in without evidence):

- **POS.** RBI built a proprietary POS (rPOS) and publicly intended to expand it across Burger King and Popeyes — a `lifecycle_state` sequence of `pilot`/`rollout_active` at limited scope, not `vendor_platform_discontinued` by default; that label requires its own evidence. Burger King separately selected PAR POS/Brink for 7,000+ North American restaurants — a `selected`/`contracted` state with an `announced_scope`, whose rollout included a documented restart (`rollout_paused` → `rollout_restarted`) before reaching `rollout_scaled`. Model this as the sequence `proprietary build → limited deployment → strategic pivot → commercial vendor selection → rollout → rollout friction/restart → scaled deployment`, not as a single "BK uses PAR" fact. Whether rPOS specifically "failed," and why RBI didn't expand it further, remain open research questions requiring their own sourced `technology_change_events` entry — never assumed.
- **Back office.** Burger King reportedly had 10+ approved back-office products before 2024 (Restaurant365, PAR Data Central, RTI/RTIconnect — normalized in RBB as Genius Back Office — and others still to be reconstructed) — an `approved_vendor_list` governance state, not `mandated`. PAR subsequently won the enterprise back-office standard and began a Data Central rollout (`selected`/`rollout_active` at the brand level), but full penetration has not been independently verified, and large operators are known to still run competing BOH platforms. **PAR's enterprise win is not the same fact as PAR's full penetration** — record the enterprise-standard observation and the installed-base observations (e.g. GPS Hospitality's ~400 Burger King locations on R365, an *operator-level* `technology_penetration` row that must not be read as establishing the brand-wide figure) as separate, coexisting `technology_penetration.jsonl` rows, not as a single number.
- **Parent company scope.** RBI owns Burger King, Popeyes, Tim Hortons, and Firehouse Subs — common ownership does not imply common technology. A finding about one RBI brand must never be propagated to the others without its own independent evidence; this is exactly what `relationship_key.parent_entity_id` plus per-brand records (rather than a parent-level technology fact) is for.

### Account management application (future consumer view, not built in this phase)

Once enough `technology_relationship_events`/`technology_penetration` evidence exists, an account-management view should be derivable (not stored separately — computed from the records above) showing, e.g.: enterprise status, eligible estate size, contracted count, verified-live count, estimated penetration range, rollout velocity, known competing installed base, remaining addressable estate, mandate status, grandfathering status, last-verified date, and confidence. This lets an account manager track win → rollout → penetration → saturation → renewal instead of treating a contract signature as the end state. **Not implemented in this phase** — noted here so the underlying records above are shaped to support it later without a schema change.

### `technology_forcing_signals.jsonl` — standalone pre-change signal log

Same append-only/supersedes discipline. Captures an observation about a brand's *current* stack that hasn't (yet, or ever) led to a completed switch — the raw material a future change-propensity model would consume. Not itself a `technology_change_events` entry.

```json
{
  "signal_id": "tfs-chipotle-pos-2026-01",
  "brand_entity_id": "brand-chipotle",
  "operator_entity_id": null,
  "entity_level": "brand | operator | location",
  "technology_category": "pos",
  "forcing_event_type": "os_eol | hardware_eol | pos_software_eol | vendor_support_sunset | compliance | peripheral_incompatibility | franchise_mandate | leadership_change | other",
  "detail": "...",
  "evidence": "...",
  "source_url": "...",
  "confidence": "high | medium | low",
  "evidence_type": "vendor_claim | operator_statement | independent_evidence | rbb_inference | unknown",
  "observed_at": "2026-01-15",
  "last_verified_current_date": "2026-01-15",
  "visibility_class": "public_shared",
  "supersedes": null
}
```

### `category_tenure_benchmarks.json` — derived, never hand-edited

Regenerated from `technology_change_events.jsonl` (Phase 1, not yet implemented). Per technology category (and optionally per segment — QSR/fast-casual/casual, enterprise/SMB, Windows/Android, franchise-heavy/corporate-heavy): `mean`, `median`, `p25`, `p75`, `n`. A category/segment with `n` below `minimum_sample_size` (default 5) must report `"insufficient_sample"` rather than publishing a statistic from too few events — the stub file's own `minimum_sample_size` field is the enforced floor once the regeneration script exists.

### Explicitly deferred (not part of this schema yet)

The numeric Change Propensity Score, its variable weighting, and the false-positive/non-switch classifier are intentionally not modeled here — they require enough real switch/non-switch sample size first (Todd's 2026-10-01 scoping decision), and should not be calculated until the event corpus is large and balanced across switches, renewals, abandoned projects, and non-switches. `technology_forcing_signals.jsonl` and `technology_penetration.jsonl` exist now precisely so that evidence is already accumulating by the time that layer is built. The account-management rollup view above is likewise a future derived view, not a new store.
