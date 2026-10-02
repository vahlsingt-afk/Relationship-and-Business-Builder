# Blue Sheet Field Dictionary (v0.3 draft)

Defines every field referenced in `standard_blue_sheet_schema.json` and in the account dossier files (`account.json`, `brand_profile.json`, `evidence.jsonl`, `actions.json`, `contradictions.json`). This is a draft — see `standard_blue_sheet_schema.json`'s `not_yet_implemented` list and the Section 7 v1.0 checklist for what's still open.

## Field-object shape

Every material fact anywhere in the dossier uses this shape (per spec Section 5):

| Key | Meaning |
|---|---|
| `value` | The fact itself. |
| `status` | One of `confirmed`, `observed`, `reported`, `hypothesis`, `unknown`, `contradicted`. |
| `evidence_ids` | Array of `evidence_id` values from `evidence.jsonl` supporting this fact. Empty array means no supporting evidence yet — `status` must then be `hypothesis` or `unknown`, never `confirmed`. |
| `confidence` | `high`, `medium`, or `low`. |
| `as_of` | ISO-8601 date the fact was true/observed as of — not the ingestion date. |
| `scope` | `account`, `opportunity:<id>`, `channel:<id>`, or `entity:<id>`. Determines whether a fact belongs in `brand_profile.json` (account scope) or an opportunity's section of `account.json` (opportunity/channel/entity scope). |
| `last_reviewed_by` | `human:<name>` or `automation:<version>`. |

## Status vocabulary

- **confirmed** — the customer has explicitly and directly stated or documented this.
- **observed** — directly witnessed by us (e.g. a meeting attendee, a live technology endpoint).
- **reported** — stated by the customer or a public source, but not independently observed or scope-verified.
- **hypothesis** — our own analytical judgment; explicitly not customer-confirmed. All Miller Heiman role/mode/preference/rating fields default to `hypothesis` unless the customer has directly stated their own position.
- **unknown** — no evidence either way. Never blank — `unknown` is an explicit, visible state per spec Section 16.
- **contradicted** — two or more evidence records disagree and have not been reconciled; see `contradictions.json`.

## Evidence class vocabulary (from spec Section 6, in default precedence order)

1. `executed_customer_document` — an RFI/RFP, signed contract, or other document the customer executed.
2. `customer_provided_operational_data` — statements, exports, or data the customer supplied.
3. `direct_observation` — we directly observed the customer's live environment.
4. `direct_customer_correspondence` — an email, call, or message directly from the customer.
5. `seller_authored_meeting_recap` — our recap of a meeting, not yet acknowledged by the customer in writing.
6. `official_public_source` — a corporate release, public filing, or public profile.
7. `internal_field_intelligence` — a field report, secondhand account, or internal observation not yet verified with the customer.
8. `analyst_hypothesis` — our own judgment or estimate.
9. `unknown` — source type not yet classified.

Recency never automatically outranks a higher-precedence class — reconcile scope first (see `contradictions.json`'s `ct-pollo-campero-0001` for a worked example: a statement sample and a field report can both be correct for different stores/entities).

## Blue Sheet is downstream of the canonical registry (decided 2026-08-21)

The Blue Sheet is never the canonical account/opportunity record — it is created only for a targeted account with a specific sales motion, only on Todd's explicit direction, and it draws heavily on the existing RBB canonical registry (`system/account_intelligence/`, `system/ecosystem_intelligence.json`, `system/baseline_index.json`, `system/loop_ledger.md` + `system/eolms/loops.json`, etc. — see the `accounts`/`opportunities`/`execution_loops` domains in `system/CANONICAL_REGISTRY.yaml`) once it exists. Every field below is one of:

- **registry-sourced** — the fact already exists upstream in the canonical registry. Sync it in on every relevant canonical-registry mutation; never hand-edit it only inside `blue_sheets/`, or the two will drift.
- **blue_sheet-native** — a Miller Heiman interpretive judgment that has no upstream equivalent and only ever lives in the Blue Sheet layer (it may itself become a new piece of upstream evidence if the judgment changes, but the field itself is not canonical account data).

## account.json top-level fields

| Field | Meaning | Provenance |
|---|---|---|
| `account_id` | Stable ID, `acct-<slug>`. Never changes even if `display_name` changes. | registry-sourced |
| `account_slug` | Lowercase, hyphenated, folder-name-safe. Never derived solely from a display-name change (spec Section 4). | registry-sourced |
| `portfolio_status` | `active` or `parked` — drives the Section 16 portfolio policy. | registry-sourced |
| `opportunities[]` | One entry per opportunity/workstream. Each carries its own `single_sales_objective`, `customer_stated_objective`, and `commercial_hypothesis` as field-objects. | `single_sales_objective`/`commercial_hypothesis` are blue_sheet-native; `customer_stated_objective` is registry-sourced when the customer document exists upstream |
| `qualification` | The five Miller Heiman scorecard criteria plus the computed score. See `scoring_rules.json`. | blue_sheet-native |
| `strategic_position` | Euphoria-Panic, Competition, and Position sections of the Blue Sheet tab. | blue_sheet-native |
| `buying_influences[]` | Full stakeholder table. Each person has a `person_id`; governance-sensitive sub-fields (`role_etuc`, `mode`, `personal_win`, `competitive_preference`, `rating`) are field-objects individually, since each is independently approval-gated per Section 8. | name/title/location/current_read/access/owner are registry-sourced when the person exists upstream in identity_relationships; role_etuc/mode/personal_win/competitive_preference/rating are always blue_sheet-native |
| `technology_stack[]` | One row per technology/vendor layer. | registry-sourced (from industry_ecosystem_intelligence / account_intelligence where available) |
| `commercial_models[]` | One row per commercial/economic line item. | mixed — observed volumes/rates are registry-sourced if the upstream data exists; pricing hypotheses are blue_sheet-native |
| `latest_review` | Owner, contributors, last/next review dates, and the current critical test — mirrors the Method & Governance tab's per-account block. | blue_sheet-native |
| `template_version` | Which `_standard/template_version.json` version this account's dossier/workbook conforms to. | blue_sheet-native |

## Governance-gated fields (require human approval per Section 8)

Economic Buying Influence status, Coach status, personal win, mode, competitive preference, influence rating, and final authority/veto power. These are never set to `confirmed` by automation — only a human reviewer can promote them out of `hypothesis`/`unknown`. See `person.governance_note` on Jorge de la Parra in Pollo Campero's `account.json` for a worked example of a stakeholder who is central and responsive but explicitly not yet a Coach.

## actions.json fields

Each action carries `action_id`, `opportunity_id`, `type` (`commitment` / `decision` / `risk` / `opportunity` / `dependency`), `party`, `description`, `owner`, `target`, `status`, `blocker`, `evidence_ids`, `closure_criteria`, `rbb_loop_id`, and `mutation_status`. `mutation_status: "proposed_add_loop"` means the action exists in the dossier but has **not** been mutated into a real RBB loop — current RBB mutation support is limited to `add_loop` and `close_loop` (spec Section 13), so anything else (re-dating, editing) stays a proposal until those contracts exist.
