# Standard Blue Sheet — Changelog

## v0.3 — 2026-08-21

- Established the governed `blue_sheets/` folder contract (spec Section 4).
- Confirmed the actual reference implementation: `Pollo_Campero_Blue_Sheet.xlsx`, provided by Todd on 2026-08-21, superseding the two blank Worldpay-named Miller Heiman template files found during the initial inventory (`outputs/019fce92-.../Worldpay_Blue_Sheet_Account_Plan.xlsx` and `_v2.xlsx`, both empty).
- Documented the actual 11-tab structure in `standard_blue_sheet_schema.json` (Blue Sheet, Brand, Technology Stack, Actions & Decisions, Commercial Model, Presentation View 1, Presentation View 2, Method & Governance, Criteria Statements, Concept Card side 1, Concept Card side 2) — this differs from the spec draft's 8-tab list; see the schema file's `note_on_tab_list`.
- Authored `scoring_rules.json` (qualification scorecard formula and buying-influence vocabulary) and `field_dictionary.md` (field-object shape, status/evidence-class vocabularies, governance-gated fields) from the reference workbook's actual content and its own Method & Governance / Criteria Statements tabs.
- Migrated Pollo Campero into the repository as the first full dossier: `account.json`, `brand_profile.json`, `evidence.jsonl` (12 evidence records), `actions.json` (14 actions), `contradictions.json` (1 open contradiction), `source_index.json`. Source workbook copied (never edited in place) into `accounts/pollo-campero/sources/` and `current/`.
- Created thin account shells for Five Guys and Del Taco (named in RBB loop `L-2026-08-10-005` as the rest of the first cohort) — intentionally left without fabricated brand/evidence content pending real source material.
- Built the genericized blank `Standard_Blue_Sheet.xlsx` (bracketed prompts + one example row, formulas intact but not recalculated — no LibreOffice available in this environment) and the `_portfolio/` registry files (`active_accounts.json`, `blue_sheet_registry.json`, `review_queue.json`).
- Wrote the Phase-1 gap report (`GAP_REPORT_2026-08-21.md`) and added a `blue_sheets` domain to `system/CANONICAL_REGISTRY.yaml`, flagging (not assuming) its overlap with the still-unresolved canonical account/opportunity registry decisions.
- Not yet done: deterministic renderer, validator, event-impact-review routine, portfolio health workbook. See the gap report for the full list.

## v0.3 — 2026-08-21 (continued: engine build)

- Built `_engine/render.py`, `_engine/validate.py`, `_engine/impact_review.py`, `_engine/common.py` — a working renderer/validator/impact-review loop, tested against Pollo Campero.
- Proved the safe-apply/approval-queue split end to end with a synthetic dry-run event, then reverted all synthetic artifacts (evidence record, dossier field, review-queue item, one history archive) so the live account reflects only real data. See `GAP_REPORT_2026-08-21.md` for the full account of what ran and what was undone.
- Added `strategic_position.strengths[]` / `.red_flags[]` to `account.json`'s schema so the renderer has a structured source for the Blue Sheet tab's summary block (previously only implicit in free text).
- Remaining gap: none of this is wired into the live RBB canonical-registry mutation scripts yet — `impact_review.py` must be called explicitly. See the gap report's "still not built" list.

## PARKED — 2026-08-21 (Todd)

Todd asked to park this initiative for the next sprint: too many concurrent threads right now. Everything above (folder contract, standard template, Pollo Campero dossier, engine, architectural decision) is real, tested, and left in a clean, consistent state — nothing half-applied. Explicitly not started: wiring `_engine/impact_review.py` into the live RBB canonical-registry mutation scripts. A background investigation of those scripts' mutation entry points, account_id availability, and existing hook patterns (`mutations.py`, `ri_events.py`, `ecosystem_intelligence.py`, `eolms.py`) was in flight when the park request came in; its findings, if useful, will be folded in here or into the gap report at the start of next sprint rather than acted on now. Resume by reading this file, `GAP_REPORT_2026-08-21.md`, and the `blue_sheets` domain in `system/CANONICAL_REGISTRY.yaml` — that's the full state, no other context needed.

## Architectural decision — 2026-08-21 (Todd)

- The Blue Sheet is downstream of the canonical account/opportunity registry, not a replacement for it. A Blue Sheet is created only for a targeted account with a specific sales motion, and only on Todd's explicit direction — never automatically from the Section 16 active-portfolio policy alone.
- Once a Blue Sheet exists for an account, the canonical registry becomes a major, ongoing source of updates to it.
- Repeatable pipeline: a canonical-registry mutation checks whether the touched account has a Blue Sheet; if so, it triggers a Blue Sheet impact review (auto-apply safe/registry-sourced updates, queue blue_sheet-native/judgment changes for approval); if not, nothing happens.
- Encoded in `system/CANONICAL_REGISTRY.yaml` (`blue_sheets` domain: `architectural_decision`, `field_provenance_split`, `activation_policy`, `mutation_owner`), in `blue_sheet_registry.json` (`activation_authorized_by`/`activation_date`), and in `field_dictionary.md` (registry-sourced vs. blue_sheet-native provenance per field).
