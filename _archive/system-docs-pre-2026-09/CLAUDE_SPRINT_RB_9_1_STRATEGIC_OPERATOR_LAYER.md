# RB 9.1 Sprint Spec — Strategic Operator Persistent Layer

**Date written:** 2026-05-22
**Designer:** Claude
**Implementer:** Codex (per OPERATIONALIZATION.md role contract — Codex owns files/scripts/API/runtime)
**Status:** Aligned with Codex 2026-05-22 (alignment memo accepted: split from market_signals, categorical buckets first, 9.1/9.2 split, no further daily_brief.py expansion)

---

## Sprint Theme

Move strategic-operator intelligence out of `market_signals.py` (where it currently lives as bolted-on fields) and into a **persistent entity lane** that the system can read, write, query, and reason about as canonical state.

The product principle that drives this: a strategic operator like Flynn Group exists as an entity even on days when no news moves it. Its baseline (companies owned, executives, known vendor relationships, relationship proximity) is the durable thing. News events that affect the entity (acquisitions, leadership moves, expansion) are *movements recorded against* the entity, not the entity itself.

When Todd asks "what's our exposure to Flynn Group?", RB should read a record — not scan news.

## Scope

In scope:

- Canonical state file for strategic operators (yaml).
- Schema + validator.
- Mutation contract (`mutations.py operator-*`).
- Compute module (`strategic_operators.py`) that joins canonical state + market_signals news lane + active_threads + baseline.
- Categorical scoring rubric (no numeric scores until calibrated).
- Daily Brief section split: `strategic_operator_movements` becomes a first-class canonical section computed from the new module.
- One API endpoint (dual-mode POST) — GPT cap maintained by dropping `getRecentTestTraces`.
- Settings additions and P-031 protocol.

Out of scope (deferred to RB 9.2 source-instrumentation sprint):

- macOS Full Disk Access wiring for Python.
- Personal Gmail + calendar raw connector capture.
- Real LinkedIn messaging export ingest (parser is built; needs an export dropped in).
- End-to-end morning path test (push → GPT → daily brief command).

Also out of scope (deferred further):

- Composition layer (draft-in-voice).
- DRR eval framework.
- Protocol files for the last-sprint modules (P-025–P-030).
- Numeric scoring with calibration data.

## Files to Add

```
system/strategic_operators.yaml                      # canonical state (new)
system/schemas/strategic_operators.schema.json       # JSON schema for validation
system/scripts/strategic_operators.py                # compute module + smoke
system/scoring/strategic_operator_rubric.md          # categorical rubric (new dir)
system/protocols/P-031_strategic_operator_intelligence.md
system/CLAUDE_HANDOFF_RB_9_1_COMPLETE.md             # produced at sprint close
```

## Files to Modify

```
system/scripts/mutations.py        # +operator-add/update/record-movement/close
system/schemas/validate.py         # extend to validate strategic_operators.yaml
system/scripts/market_signals.py   # remove strategic-operator fields (migrate to new module)
system/scripts/daily_brief.py      # new section composes from strategic_operators.py
system/api/server.py               # +applyOperatorMutation endpoint
system/api/openapi.yaml            # +applyOperatorMutation
system/api/openapi_gpt.yaml        # +applyOperatorMutation, -getRecentTestTraces
system/scripts/api_smoke_test.py   # +smoke for the new endpoint
system/settings.json               # +daily_briefing.strategic_operators block
system/STATUS.md                   # RB 9.1 row updates at sprint close
```

---

# Priority 1 — `strategic_operators.yaml` + schema + validator + initial entries

**Goal:** Canonical state file alongside `active_threads.yaml`. Hand-written initial entries seed the watchlist; the mutation API (Priority 2) handles updates from there forward.

## File: `system/strategic_operators.yaml`

```yaml
version: 1
last_updated: 2026-05-22
contract: rb_strategic_operators_v1
description: |
  Persistent watchlist of strategic operator entities (multi-brand operators,
  PE-backed groups, expansion leaders, high-influence franchisees, holding
  companies). Each entity carries its baseline state, a movement history, and
  resolved relationship proximity. Updated via mutations.py operator-* or by
  hand-editing this file (PyYAML round-trips it; the validator runs on every
  write).

operators:
  - id: flynn-group
    name: Flynn Group
    entity_type: multi_brand_operator        # see schema enum
    watchlist_bucket: strategic_operator     # see schema enum
    status: active                           # active | dormant | closed
    opened: 2026-05-22
    last_movement_at: null
    companies_owned:
      - "Applebee's franchisee (largest)"
      - "Taco Bell franchisee"
      - "Panera Bread franchisee"
      - "Arby's franchisee"
      - "Wendy's franchisee"
      - "Pizza Hut franchisee (UK)"
    brands_in_portfolio:
      - applebees
      - taco-bell
      - panera
      - arbys
      - wendys
      - pizza-hut
    executives: []                            # baseline contact_ids when known
    vendor_relationships:
      - vendor: Oracle
        status: known
        evidence: industry-known POS deployment
      - vendor: PAR Technology
        status: historical
        evidence: PAR Punchh loyalty rollout
    relationship_proximity: none              # direct | one_hop | two_hop | none
    proximity_evidence: []
    notes: |
      Largest franchisee in the United States. Multi-brand. Operates ~2,400
      restaurants. Strategic relationship target — direct contact path
      unknown.
    movements: []                             # appended via operator-record-movement

  - id: carrols-restaurant-group
    name: Carrols Restaurant Group
    entity_type: pe_backed
    watchlist_bucket: pe_backed_groups
    status: active
    opened: 2026-05-22
    last_movement_at: 2024-05-16              # Restaurant Brands Intl acquisition close
    companies_owned:
      - "Burger King franchisee (largest)"
      - "Popeyes franchisee"
    brands_in_portfolio:
      - burger-king
      - popeyes
    executives: []
    vendor_relationships: []
    relationship_proximity: none
    proximity_evidence: []
    notes: |
      Acquired by Restaurant Brands International May 2024. ~1,000 Burger
      Kings + Popeyes. PE-adjacent through RBI.
    movements: []

  # Additional initial entries (one per line; trim or expand as the operator wants):
  # - sailormen
  # - ghai-management
  # - thrive-restaurant-group
  # - sizzling-platter
  # - kbp-foods
  # - muy-companies
  # - npc-successor-entities (split per actual entity)
```

Six initial entries minimum. Document `entity_type` and `watchlist_bucket` enums in the schema below; the architecture doc already names the buckets.

## File: `system/schemas/strategic_operators.schema.json`

JSON Schema Draft 7+. Lock the field set so hand-edits + mutations both validate.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Strategic Operators",
  "type": "object",
  "required": ["version", "contract", "operators"],
  "properties": {
    "version": {"type": "integer", "minimum": 1},
    "last_updated": {"type": "string", "format": "date"},
    "contract": {"const": "rb_strategic_operators_v1"},
    "description": {"type": "string"},
    "operators": {
      "type": "array",
      "items": {"$ref": "#/definitions/operator"}
    }
  },
  "definitions": {
    "operator": {
      "type": "object",
      "required": ["id", "name", "entity_type", "watchlist_bucket", "status", "opened"],
      "additionalProperties": false,
      "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{1,80}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "entity_type": {
          "type": "string",
          "enum": [
            "multi_brand_operator",
            "pe_backed",
            "regional_scale",
            "franchisee_group",
            "holding_co",
            "consolidator",
            "expansion_leader"
          ]
        },
        "watchlist_bucket": {
          "type": "string",
          "enum": [
            "strategic_operator",
            "multi_brand_operator",
            "expansion_leader",
            "high_influence_franchisee",
            "pe_backed_groups"
          ]
        },
        "status": {"type": "string", "enum": ["active", "dormant", "closed"]},
        "opened": {"type": "string", "format": "date"},
        "closed_at": {"type": ["string", "null"], "format": "date"},
        "last_movement_at": {"type": ["string", "null"], "format": "date"},
        "companies_owned": {"type": "array", "items": {"type": "string"}},
        "brands_in_portfolio": {"type": "array", "items": {"type": "string"}},
        "executives": {"type": "array", "items": {"type": "string"}, "description": "baseline contact_ids"},
        "vendor_relationships": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["vendor", "status"],
            "additionalProperties": false,
            "properties": {
              "vendor": {"type": "string"},
              "status": {"type": "string", "enum": ["known", "historical", "rumored", "partnership"]},
              "evidence": {"type": "string"}
            }
          }
        },
        "relationship_proximity": {
          "type": "string",
          "enum": ["direct", "one_hop", "two_hop", "none"]
        },
        "proximity_evidence": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "movements": {
          "type": "array",
          "items": {"$ref": "#/definitions/movement"}
        }
      }
    },
    "movement": {
      "type": "object",
      "required": ["id", "event_at", "movement_type", "summary", "source", "confidence"],
      "additionalProperties": false,
      "properties": {
        "id": {"type": "string", "pattern": "^M-\\d{4}-\\d{2}-\\d{2}-[a-z0-9-]+$"},
        "event_at": {"type": "string", "format": "date"},
        "captured_at": {"type": "string"},
        "movement_type": {
          "type": "string",
          "enum": [
            "acquisition", "divestiture", "bankruptcy", "restructure",
            "franchise_transfer", "pe_activity", "concept_expansion",
            "geographic_expansion", "leadership_move", "technology_standardization",
            "vendor_transition", "labor_pressure", "unit_growth_velocity",
            "integration_signal", "reporting_visibility_signal"
          ]
        },
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        "source": {"type": "string"},
        "source_quality": {"type": "string", "enum": ["primary", "vertical_trade", "mainstream", "rumor"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "inferences": {
          "type": "object",
          "additionalProperties": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
          "description": "Categorical inferences keyed by dimension: operational_complexity_increase, standardization_pressure, technology_reassessment, scaling_strain, integration_risk, labor_coordination_pressure, reporting_visibility_needs, vendor_consolidation_likelihood, buying_window_probability"
        }
      }
    }
  }
}
```

## Validator extension

`system/schemas/validate.py` currently validates `baseline_index.json`. Extend it to also validate `system/strategic_operators.yaml` (read with PyYAML, validate the parsed dict against the schema). Surface failures with line context.

CLI behavior: `python3 system/schemas/validate.py` validates *both* files and fails non-zero if either fails. Optional flag `--baseline-only` for the existing narrower check.

## Acceptance

- The file parses with PyYAML.
- `python3 system/schemas/validate.py` returns 0.
- Six initial entries pass.
- One synthetic invalid entry (e.g., `entity_type: "invalid"`) caught by the validator with a useful error message.

---

# Priority 2 — `mutations.py operator-*` commands + `applyOperatorMutation` endpoint

**Goal:** Programmatic updates to `strategic_operators.yaml` go through the same snapshot-then-validate-then-rollback discipline as every other write (P-009).

## Four CLI subcommands

```bash
python3 system/scripts/mutations.py operator-add \
  --id ghai-management --name "Ghai Management" \
  --entity-type multi_brand_operator --watchlist-bucket multi_brand_operator \
  --notes "Recently acquired 44 Taco Bell locations."

python3 system/scripts/mutations.py operator-update \
  --id flynn-group --relationship-proximity one_hop \
  --add-executive bob-gibson \
  --note "Connected via Bob Gibson at Toast."

python3 system/scripts/mutations.py operator-record-movement \
  --operator-id ghai-management \
  --event-at 2024-08-15 \
  --movement-type acquisition \
  --summary "Acquired 44 Taco Bell locations in Northeast." \
  --source "Restaurant Business" --source-quality vertical_trade \
  --confidence high \
  --inference operational_complexity_increase=high \
  --inference standardization_pressure=high \
  --inference buying_window_probability=medium

python3 system/scripts/mutations.py operator-close \
  --id npc-international --reason "Wound down; estate dissolved." \
  --closed-at 2024-12-31
```

Each subcommand:

1. Snapshots `strategic_operators.yaml` into `system/_snapshots/strategic_operators.<pre-op-tag>.yaml`.
2. Applies the change.
3. Runs the schema validator. On failure: restore snapshot, exit non-zero, log the validation error.
4. Updates `last_updated` field at file top and `last_movement_at` on the touched entity when applicable.
5. Emits a single-line success message.

All four accept `--dry-run` (per the smoke-test-mutations memory).

Movement IDs: auto-generated as `M-YYYY-MM-DD-<slug>` (slug from summary first 20 chars).

## One API endpoint

`POST /strategic_operators/apply` → `applyOperatorMutation` (dual-mode per the dual-mode-POST memory).

Request body:

```json
{
  "confirm": false,
  "action": "add | update | record_movement | close",
  "operator_id": "flynn-group",
  "fields": { /* action-specific payload */ }
}
```

`confirm=false` returns the resolved diff (current entity state + proposed new state) with no writes. `confirm=true` runs the corresponding `mutations.cmd_operator_*`.

## GPT cap swap

To stay under 30 ops, drop `getRecentTestTraces` from `openapi_gpt.yaml` (Codex's first-choice candidate; agreed). Add `applyOperatorMutation` to the GPT subset.

Net: GPT spec stays at 30. Full spec gains 1 op (54 total).

## Acceptance

- All four subcommands round-trip through dry-run cleanly.
- Schema-failing mutations (e.g., invalid `entity_type`) trigger automatic rollback from snapshot.
- `validate_openapi_gpt.py` returns OK at 30 ops.
- `api_smoke_test.py` includes a `confirm=false` smoke for the new endpoint with a no-write contract assertion (ledger row count unchanged before/after).

---

# Priority 3 — `strategic_operators.py` compute module + categorical scoring rubric

**Goal:** Read canonical state + the news lane + active threads + baseline, and produce a structured overlay the Daily Brief renders.

## Module: `system/scripts/strategic_operators.py`

Public surface:

```python
def load_operators() -> list[dict]:
    """Parse strategic_operators.yaml. Empty list when file missing."""

def operator_overlay(*, today: date | None = None,
                     baseline: list[dict] | None = None,
                     active_threads: list[dict] | None = None,
                     market_signals_payload: dict | None = None) -> dict:
    """Return the overlay used by the daily brief.

    Shape:
      {
        "contract": "strategic_operators_overlay_v1",
        "today": "YYYY-MM-DD",
        "totals": {
            "active": int,
            "with_recent_movement_30d": int,
            "direct_proximity": int,
            "one_hop_proximity": int,
        },
        "active_operators": [
            {
              "id": ...,
              "name": ...,
              "entity_type": ...,
              "watchlist_bucket": ...,
              "relationship_proximity": ...,
              "proximity_evidence": [...],
              "executives_in_baseline": [{"id":..., "name":..., "signal_class":...}, ...],
              "mutual_connections": [...],   # from baseline + threads
              "recent_movements": [...],     # last 90d from .movements
              "candidate_movements": [...],  # market_signals rows that mention this entity but
                                             # aren't yet recorded as a movement (reconciliation prompt)
              "scoring": {
                  "influence": "low|medium|high|critical",
                  "operational_pressure": "low|medium|high|critical",
                  "ecosystem_impact": "low|medium|high|critical",
                  "future_opportunity": "low|medium|high|critical",
                  "follow_up_priority": "low|medium|high|critical"
              },
              "rubric_evidence": {
                  "influence": "rule_2: multi_brand_operator + companies_owned>=4",
                  ...
              },
              "ref": "strategic_operators.<id>"
            },
            ...
        ],
        "reconciliation_prompts": [
            # When market_signals mentions an entity not in the watchlist:
            {
              "kind": "candidate_operator",
              "company": "...",
              "evidence": "market_signals.<source>",
              "prompt": "X mentioned in <N> market_signals rows in last 30d; promote to strategic_operators?",
              "recommended_mutation": "operator-add --id=<slug> --name='<name>' --entity-type=<type> ..."
            },
            ...
        ],
        "stale_or_missing": {
            "file_exists": bool,
            "operators_with_zero_movements": int,   # might be a coverage gap or genuinely quiet
            "ambiguous_proximity": [...]            # ids needing operator review
        }
      }
    """

def render_canonical_items(overlay: dict) -> list[dict]:
    """Convert overlay rows into canonical_item dicts the daily brief appends
    to sections['strategic_operator_movements']. Each item carries title,
    summary, why_it_matters, recommended_action, disposition, grounding,
    freshness, source_refs, confidence, action_options."""
```

CLI: `python3 system/scripts/strategic_operators.py [--overlay | --canonical-items | --smoke] [--json]`.

`--smoke` does in-memory regression with synthesized operators + threads + baseline; no I/O; ≥20 assertions.

## Relationship-proximity resolution rules

These are deterministic and run inside `operator_overlay`. Any failure to resolve → `relationship_proximity` stays at whatever was in the yaml; the rubric uses the explicit value or `unknown`.

```
direct:   any executives[] id is an RC in baseline
one_hop:  any executives[] id is in baseline (any signal_class)
          OR any RC in baseline has current_company matching a companies_owned entry
          OR any open active_thread.companies overlaps brands_in_portfolio
two_hop:  any baseline contact has current_company matching a brand in portfolio
          (even if not RC)
          OR any closed-but-recent thread overlaps
none:     no overlap detected
```

The overlay reports the *resolved* proximity AND the original `relationship_proximity` field from the yaml. If they disagree → emit a `reconciliation_prompts` entry: "yaml says X, resolver says Y, which is current?"

## Categorical scoring rubric

File: `system/scoring/strategic_operator_rubric.md`.

Five dimensions. Each has four levels (`low | medium | high | critical`) defined by observable conditions. Examples (sketch — the actual doc fills these out):

### Influence

- `low` — Single-brand operator under 50 units OR no public movement in past 365 days.
- `medium` — Multi-unit, single-brand, 50–500 units OR one acquisition in past 365 days.
- `high` — Multi-brand operator (3+ brands) OR 500+ units OR PE-backed OR named as peer-influencer by another operator.
- `critical` — Multi-brand AND 1000+ units AND named in industry trade press as influencing peer behavior in past 90 days.

### Operational pressure

- `low` — Stable, no recent acquisitions, no leadership churn, no public restructure.
- `medium` — One acquisition or major expansion in past 180 days OR one executive departure.
- `high` — Two or more movements in past 180 days OR public restructure announced OR labor-pressure signal from trade press.
- `critical` — Bankruptcy filing, debt restructuring, or operational crisis in past 90 days.

### Ecosystem impact

- `low` — Operates in one brand's ecosystem, no public technology decisions, no influence on peer operators.
- `medium` — Operates in 2 brands OR has made one public technology decision affecting peer choice.
- `high` — Multi-brand operator AND public technology decisions influence peer operators in same brand.
- `critical` — Industry-shaping vendor selection (e.g., chose Oracle Symphony for 1000+ units) AND multiple peer operators followed.

### Future opportunity

- `low` — No relationship proximity, no buying window indicators.
- `medium` — One_hop proximity OR buying window indicator (post-acquisition integration phase, leadership change).
- `high` — Direct proximity OR active integration phase post-acquisition (12-month window from a recent acquisition).
- `critical` — Direct proximity AND active buying window AND Todd has positioning fit.

### Follow-up priority

Composite. Derived from the other four:

- `critical` — any of: future_opportunity=critical, operational_pressure=critical with one_hop+ proximity.
- `high` — any of: future_opportunity=high; two of {influence=high, ecosystem_impact=high, operational_pressure=high} with one_hop+ proximity.
- `medium` — future_opportunity=medium OR three of the other four at medium+.
- `low` — none of the above.

The rubric is **the** ranking source. Numeric scores are explicitly out of scope for 9.1. When calibration data exists (post-9.2, ideally after passive_verification accumulates closed-outcome data), revisit.

## Acceptance

- `strategic_operators.py --smoke` returns 0 with ≥20 in-memory checks.
- Overlay computed against real `strategic_operators.yaml` returns deterministically.
- For each initial entry: scoring resolves to a level in each dimension based on the rubric.
- Reconciliation prompts surface when yaml proximity disagrees with resolver.

---

# Priority 4 — Daily Brief section split + market_signals migration

**Goal:** `strategic_operator_movements` becomes its own first-class canonical section, computed from `strategic_operators.py`. `market_signals.py` reverts to a pure news lane.

## Section split

Current state: `daily_brief.py` adds `strategic_operator_movements` items inline alongside `condensed_industry_context`, both sourced from `market_signals.py`.

After 9.1:

```
canonical_brief.sections.strategic_operator_movements   # from strategic_operators.py
canonical_brief.sections.market_news                    # was condensed_industry_context, vendor/macro only
                                                        # (rename for clarity; alias the old key for backward compat)
```

`section_order` keeps `strategic_operator_movements` BEFORE `market_news` — the operator lane is relationship-grounded; news is subordinate.

## `daily_brief.py` changes (minimal — per Codex's "do not expand daily_brief.py further")

- Add a 6-line block that calls `strategic_operators.operator_overlay()` and `strategic_operators.render_canonical_items()`. Append into `sections["strategic_operator_movements"]`.
- Move the existing `condensed_industry_context` population block into a renamed key `market_news`. Keep the old key as an alias (`sections["condensed_industry_context"] = sections["market_news"]`) for backward compat through one sprint.
- Remove the strategic-operator field handling from `market_signals.py`'s render path — those fields are operator state, not market signal state.

The actual logic lives in the new module. `daily_brief.py` is just a section composer.

## `market_signals.py` migration

Strip these fields (they migrate to operator movements via `operator-record-movement`):

- `strategic_operator_entity_type`
- `strategic_operator_movement`
- `influence_score`, `relationship_value_score`, `operational_pressure_score`, `ecosystem_impact_score`, `future_opportunity_score`
- `relationship_proximity`, `known_network_overlap`, `mutual_connections`
- `existing_vendor_relationships`
- `buying_window_probability`, `follow_up_priority`
- `watchlist_bucket`
- The `signal_layer: strategic_operator_movement` branch + the +20 scoring bonus

Keep:

- `company`, `category`, `side`, `source_type`, `source_quality`, `event_at`, `signal_type`, `macro_force`, `pain_point_or_priority`, `strategic_relevance`, `affected_relationships_or_threads`, `restaurant_operator_impact`, `restaurant_tech_vendor_implication`, `second_order_impact`, `relationship_opportunity`, `why_this_matters_to_todd`, `recommended_action`, `timing_priority`, `confidence`.

These are the news-lane fields. `market_signals` becomes a clean ephemeral feed — vendor moves, macro context, executive interviews, conference signals.

## Data migration

For each row in `system/inbox/market_signals.json` that currently carries `strategic_operator_*` fields:

1. Look up the entity (by `company` name) in `strategic_operators.yaml`. If found → record movement via `operator-record-movement` (with `--source-quality` from the original row + `--confidence high` if the original row was authored manually).
2. If not found → emit a `reconciliation_prompts` entry on the next brief run ("X mentioned but not in watchlist; promote?").
3. Strip the operator-only fields from the original row in place.

A one-shot CLI: `python3 system/scripts/strategic_operators.py --migrate-from-market-signals --confirm`. Default is dry-run; `--confirm` writes.

## Acceptance

- `daily_brief.py --smoke` still 0 failures.
- New canonical section `strategic_operator_movements` populates from `strategic_operators.py` overlay.
- `market_news` section continues to render news items but no longer carries any strategic-operator fields.
- A migration dry-run on the current `system/inbox/market_signals.json` reports exactly which rows would emit which operator movements (auditable preview).

---

# Priority 5 — Settings, P-031 protocol, STATUS, handoff

## `system/settings.json` additions

Under `daily_briefing`, add:

```json
"strategic_operators": {
  "enabled": true,
  "watchlist_path": "system/strategic_operators.yaml",
  "movement_recent_days": 90,
  "auto_record_from_market_signals": false,
  "auto_record_note": "Operator-curated for v1. When auto-record lands, it will propose movements via reconciliation_prompts; operator confirms via operator-record-movement.",
  "proximity_resolver": {
    "enabled": true,
    "report_disagreements": true
  },
  "scoring": {
    "mode": "categorical_rubric",
    "rubric_path": "system/scoring/strategic_operator_rubric.md",
    "numeric_scores_enabled": false,
    "numeric_scores_note": "Numeric scores deferred until calibration data exists (post-9.2, after passive_verification closures accumulate)."
  }
}
```

## Protocol: `system/protocols/P-031_strategic_operator_intelligence.md`

Lock the contract. Standard frontmatter (script, cache, reads, writes, trigger). Sections:

- Purpose
- Read set (strategic_operators.yaml, market_signals.json, active_threads.yaml, baseline_index.json)
- Compute steps (1: load operators; 2: compute proximity; 3: compute scoring; 4: surface candidate movements from market_signals; 5: emit reconciliation prompts; 6: render canonical items)
- Mutation contract (links to P-009)
- Failure modes (file missing → empty overlay, schema invalid → halt, proximity ambiguous → reconcile)
- Voice

## STATUS.md updates

After sprint close: a new compute-layer row for `strategic_operators.py` with smoke count + the role it plays; mark `market_signals.py` as "news lane only after RB 9.1 migration"; bump the last-reviewed date; add P-031 row.

## Final handoff: `system/CLAUDE_HANDOFF_RB_9_1_COMPLETE.md`

Mirrors the 2026-05-22 execution-layer handoff format: priorities completed, file inventory, validation status, host-side finish (commit sequence), and next-sprint candidates (RB 9.2 source-instrumentation + composition layer queued).

## Acceptance

- All smokes still green.
- `validate_openapi_gpt.py` OK at exactly 30 ops.
- Settings.json + schemas valid.
- P-031 file exists and is referenced from STATUS.md.
- Handoff produced.

---

# Cross-Cutting Constraints (Standing Memory)

These apply throughout. Codex already operates by them; restating for completeness:

1. **Smoke tests never reach mutation paths.** The new `--smoke` for `strategic_operators.py` and `mutations.py operator-*` must be in-memory only. The apply endpoint's smoke runs with `confirm=false` and asserts no file change.

2. **One commit per priority.** Five priorities → five commits, in the order above. Squash only if diffs interleave (Priority 4 will touch daily_brief.py + market_signals.py — that's fine for one commit).

3. **30-op GPT cap held.** Drop `getRecentTestTraces` (Codex's first choice) when adding `applyOperatorMutation`. Run `validate_openapi_gpt.py` at the end of Priority 2 to confirm.

4. **Sandbox can't commit.** Host-side `git add` + `git commit` per priority.

5. **No fabrication when data is missing.** If `strategic_operators.yaml` is missing or empty, the overlay returns zero-totals and the brief section explicitly says "no strategic operators on the watchlist yet" — never invents entries. Same Tenet-1 discipline that publish.py applies to the email CTA.

6. **No further `daily_brief.py` expansion.** New canonical sections compose data from new modules. `daily_brief.py` only changes by ~10 lines this sprint (Priority 4's small block).

---

# Out-of-Sprint Recommendations (for RB 9.2 + later)

When 9.1 ships, RB 9.2 picks up:

- **macOS Full Disk Access for Python.** Operator-facing doc; the unblocker for SMS/calls dormancy automation.
- **Personal Gmail + calendar raw connector capture.** Contract definition for how the Cowork-session fetcher drops raw_*.json per account.
- **LinkedIn export ingest.** Parser is built — needs a real export ingested. Operator action.
- **End-to-end morning-path test.** Push → GPT open → typed command → returned brief.

When 9.2 ships, RB 9.3 picks up:

- **Composition layer (draft-in-voice).** Highest-leverage *operator-UX* leap once the data foundation is complete.
- **DRR eval framework.** Now backed by enough passive_verification closure data to validate.
- **Numeric scoring for strategic operators.** Calibrated against ground truth (closed opportunities, won/lost intros).

---

# Sprint Done When

- All five priorities merged.
- `strategic_operators.yaml` carries six+ initial entries, validates.
- `strategic_operators.py --smoke` green; ≥20 in-memory checks.
- `daily_brief.py --smoke` still green; new section composes.
- `mutations.py operator-*` round-trips through dry-run for all four subcommands.
- `applyOperatorMutation` endpoint in GPT spec; `getRecentTestTraces` removed.
- `validate_openapi_gpt.py` OK at 30 ops.
- `market_signals.py` no longer carries strategic-operator fields.
- Migration dry-run runs cleanly on current inbox.
- P-031 protocol exists.
- STATUS.md + handoff updated.

That is RB 9.1.
