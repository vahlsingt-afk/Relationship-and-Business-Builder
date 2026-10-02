# Claude Handoff — Unified Restaurant-Tech Graph and Shareable Export

**Date:** 2026-08-01
**Request:** [`system/CLAUDE_REQUEST_RB_UNIFIED_RESTAURANT_TECH_GRAPH_AND_SHAREABLE_EXPORT_2026-07-31.md`](CLAUDE_REQUEST_RB_UNIFIED_RESTAURANT_TECH_GRAPH_AND_SHAREABLE_EXPORT_2026-07-31.md)
**Plan executed:** all 8 phases (Baseline → Schema → Migration Tool → Watchlist Audit → Evidence Extraction → Daily Pipeline → Export Generator → this handoff)
**STATUS.md:** updated with a full close-out entry and 5 new/updated Compute-layer rows (see `system/STATUS.md`, entry dated 2026-08-01).

---

## 1. What changed

### Schema (`system/schemas/ecosystem_intelligence.schema.json`)
- Added 4 optional relationship fields: `ai_application` (11-value enum), `deployment_status` (15-value enum), `deployment_detail` (free text), `source_assertions[]` (new `source_assertion` definition).
- Backward-compatible — all 62 existing relationships validate unchanged.

### Graph engine (`system/scripts/ecosystem_intelligence.py`)
- New `migrate-workbook <path> [--confirm]` CLI mode, dry-run by default.
- New: `RECONCILIATION_OUTCOMES` (11-value vocabulary), `WORKBOOK_DEPLOYMENT_STATUS_VALUES`, `AI_APPLICATION_VALUES`, `_HISTORICAL_DEPLOYMENT_STATUSES`.
- New: `_load_xlsx_sheet_by_name()`, `_norm_deployment_status()`, `_norm_ai_application()`, `_infer_workbook_vendor_role()`, `_merge_source_assertions()`, `_resolve_brand_entity_id()` (sheet-wide hint disambiguation), `reconcile_workbook_row()`.
- Reuses the pre-existing `check_relationship_conflict()` / `resolve_and_upsert_relationship()` conflict machinery — not rebuilt.

### Earnings monitor (`system/scripts/earnings_monitor.py`)
- New signal types: `provider_win`, `contract_renewal_expansion`, `vendor_churn_loss` (same vocabulary as `market_source_feeds.py`).
- New tiered daily fetch: `_fetch_tier()`, `fetch_company(tier=...)`, `run(tiered=...)`. Full EDGAR+IR-RSS for watch-priority/near-earnings companies; EDGAR-8K-only for everyone else.
- Fixed a bare-word false-positive risk (`"olo"` matching inside `"technology"`) before it shipped — caught in test, not production.

### Earnings calendar (`system/earnings_calendar.yaml`)
- Olo's category fixed (`restaurant_ai` → `online_ordering`).
- Expanded from 12 to 34 companies (22 added, all SEC-verified tickers/CIKs — see STATUS.md for the full list).

### Trade-press classifier (`system/scripts/market_source_feeds.py`)
- Same 3 new signal types as `earnings_monitor.py`, plus an AI-application tagger (`_classify_ai_application()`) and a contracted-vs-live deployment-stage hint (`_classify_deployment_stage_hint()`), both stamped onto every row.

### Mutation engine (`system/scripts/intelligence_mutation_engine.py`)
- `reconciliation_outcome` tagging on `vendor_customer_relationship` mutations (win/renewal/churn language → `new_relationship` / `lifecycle_update` / `supersedes_existing_relationship`). Churn mutations always require human confirmation, regardless of confidence.
- New daily driver: `refresh_ecosystem_daily()`, `generate_mutations_from_signal_row()`, new CLI mode `--refresh-ecosystem`.
- `apply_mutations()`'s graph-write path now snapshots before write and validates after (previously wrote the file directly with no safety net at all — see §4, Incident, below).

### Daily pipeline (`system/scripts/refresh_sources.py`)
- New `refresh_ecosystem_intelligence()` + `--ecosystem` flag, added to `--all` after `--earnings`/`--market`/`--watch-scan`.
- Fixed `refresh_earnings()`: was calling `earnings_monitor.py --fetch --watch-only`, which filtered the calendar down to only the always-full-tier companies *before* the new tiering logic ever ran — silently excluding every newly-added lower-priority company from the real daily pipeline. Now calls it without `--watch-only`.

### New export generator (`system/scripts/ecosystem_export.py`)
- Graph-to-workbook exporter. `--internal` / `--shareable` / `--output <path>`. Six tabs (Executive Summary, Canonical Restaurant Tech Stack, Vendor Customer List, Evidence Ledger, Macro Views, Update Playbook). Default output: `system/exports/` (git-ignored, regeneratable).

### New/extended tests
`test_ecosystem_intelligence.py` (+7), `test_ecosystem_migration.py` (new, 41), `test_earnings_monitor.py` (+11 EM11, +7 EM12), `test_market_source_feeds.py` (+7), `test_intelligence_mutation_engine.py` (new, 11), `test_ecosystem_daily_pipeline.py` (new, 10), `test_ecosystem_export.py` (new, 14).

---

## 2. Migration results — CONFIRMED live 2026-08-01

Reviewing the 6 flagged conflicts surfaced 3 real defects in the conflict-detection logic itself (not genuine business ambiguity — see `system/STATUS.md`'s 2026-08-01 follow-up entry for the full technical writeup):
1. `check_relationship_conflict()` ignored `vendor_role` entirely, so a non-competing approved-hardware/reseller/installer claim false-flagged against a system-of-record claim in the same category (HP, MAPS, NCR at McDonald's).
2. The workbook's "Deployment Status" and "Lifecycle Current State" columns disagreed for NCR/Burger King (one said current, the other said "Historical — superseded").
3. An incoming claim that explicitly admitted it was historical/superseded was still being flagged as if it competed with the active incumbent.

All 3 fixed at the code level. Two smaller bugs (a "pilot" substring match misreading a row's explicit "not a pilot" language; a processing-order artifact) were also fixed while extending vendor-role inference to read the free-text `deployment_detail` column. After the fixes, the dry-run went from 6 conflicts to 0. Confirmed against the live graph (`system/_snapshots/workbook_migration_report-20260801-083222.json`, 73 rows from the "Vendor Customer Lists" sheet):

| Outcome | Count |
|---|---|
| `new_relationship` | 6 |
| `enrich_existing_relationship` | 13 |
| `duplicate_no_change` | 0 |
| `new_source_for_existing_relationship` | 2 |
| `lifecycle_update` | 41 |
| `supersedes_existing_relationship` | 3 |
| `conflict_requires_review` | 0 |
| `working_profile_not_promoted` | 8 |
| `pilot_not_promoted` | 0 |
| `invalid_customer_or_module_excluded` | 0 |
| `entity_resolution_required` | 0 |
| **Total added / updated / conflicts / auto-superseded** | **6 / 59 / 0 / 0** |

Flagship correction is live: `rel-brand-checkers-rally-s-drive-thru-ai-unknown-vendor-presto` → `status: historical`, `deployment_status: historical_reseller_relationship_superseded`; `rel-brand-checkers-rally-s-drive-thru-ai-unknown-vendor-hi-auto` → `status: active`, `deployment_status: significant_deployed_footprint`. Both resolve to the same Checkers & Rally's brand entity. McDonald's working-profile rows (NEWPOS, QSRSoft, PAR/NCR hardware, etc.) correctly landed as `working_profile_not_promoted` (8 rows) and were never promoted.

`system/ecosystem_intelligence.json`: 1,610 → 1,611 entities (Hi Auto is a genuinely new vendor), 62 → 68 relationships. Schema-validated clean. A manual snapshot was taken immediately before the confirm run (`system/_snapshots/ecosystem_intelligence.pre-confirm-manual-20260801-083211.json`) in addition to `_write_graph()`'s own automatic pre-write snapshot. Two tests with hardcoded pre-migration expectations were updated to check the new, correct, post-migration state rather than re-asserting stale numbers.

---

## 3. Source coverage added

- **Earnings watchlist:** 12 → 34 companies (22 new, SEC-verified). Full list in `system/STATUS.md`'s 2026-08-01 close-out entry and `earnings_calendar.yaml` itself.
- **Signal vocabulary:** `provider_win`, `contract_renewal_expansion`, `vendor_churn_loss` added to both the 8-K/IR-RSS classifier and the trade-press classifier — shared vocabulary so either source feeds the same `reconciliation_outcome` tagging in the mutation engine.
- **AI-application tagging:** every trade-press signal row now carries an `ai_application` value (or `None`) from the same 11-value enum Phase 1 added to the graph schema.
- **Deployment-stage hinting:** trade-press rows are tagged `contracted_deployment_pending` / `active_rollout` / `None` based on contracted-vs-live language.

- **10-Q/10-K filing index:** `earnings_monitor.py` now also fetches EDGAR's 10-K and 10-Q Atom feeds per company (filing title/date/accession-number metadata, same depth as the 8-K fetcher), full tier only.

**Still deferred (Todd was asked and chose to defer rather than name a paid source):**
- Full-document text extraction for 10-Q/10-K (the index feed above lists filings; it doesn't fetch or parse the filing body itself — a separate, larger scope).
- Investor-presentation/PDF ingestion.
- Earnings-call transcripts — EDGAR does not host these; needs a licensed provider or manual sourcing.
- Franchise-disclosure-document ingestion — state-filed, no free central API.

These are real gaps, not oversights — revisit if Todd wants to name a specific provider/source for any of them.

---

## 4. Test results vs. Phase 0 baseline

| | Count |
|---|---|
| Phase 0 baseline (start of this build) | 3,255 passing |
| After all 8 phases | 3,315 passing |
| After migration confirm + conflict-detection fixes + 10-Q/10-K feed | 3,323 passing |
| New tests added (total) | 68 |
| Regressions | 0 |

Full suite re-run and confirmed green after every phase, not just at the end (`python3 -m pytest system/tests/ -q`).

**Incident during Phase 5 development (caught and fixed, disclosed here for the record):** an early draft of `apply_mutations()`'s graph-write path called `ecosystem_intelligence._write_graph()` directly. That function's `VALIDATOR` and `core.ECOSYSTEM_INTELLIGENCE_PATH` constants are bound to the real filesystem at import time and don't observe a test's `core.SYSTEM_DIR` monkeypatch — so two test runs briefly overwrote the real `system/ecosystem_intelligence.json` (1,610 entities → 2) with sandboxed test fixture data. Caught immediately via a routine `git diff`/entity-count check after the test run, restored from `_write_graph()`'s own automatic pre-write snapshot (`system/_snapshots/ecosystem_intelligence.pre-write-20260731-143507.json`, taken the instant before the bad write), and verified byte-equivalent to the pre-incident state. Root cause fixed by deriving every path the daily pipeline touches from named, individually-patchable module constants instead of reusing another module's stale-bound ones. No data was lost; the full test suite was re-verified green afterward, and this file's own test suite (`test_ecosystem_daily_pipeline.py`) explicitly documents and guards against the same class of bug for future changes.

---

## 5. Daily run commands

```bash
# Full daily refresh (includes the new --ecosystem step)
python3 system/scripts/refresh_sources.py --all

# Workbook reconciliation
python3 system/scripts/ecosystem_intelligence.py migrate-workbook <path>            # preview
python3 system/scripts/ecosystem_intelligence.py migrate-workbook <path> --confirm  # apply after review

# Rebuild relationship classifications (6-level model)
python3 system/scripts/relationship_classification.py classify-all

# Regenerate exports
python3 system/scripts/ecosystem_export.py --shareable
python3 system/scripts/ecosystem_export.py --internal

# Schema validation (also runs automatically before every graph write)
python3 system/schemas/validate.py --ecosystem-only
```

## 6. Export command / output path

```bash
python3 system/scripts/ecosystem_export.py --shareable
# → system/exports/ecosystem_export-shareable-<timestamp>.xlsx

python3 system/scripts/ecosystem_export.py --internal --output /custom/path.xlsx
# → /custom/path.xlsx
```
`system/exports/` is git-ignored — every file in it is regeneratable from the graph on demand, never hand-edited.

---

## 7. Remaining gaps / open items

1. **Deferred evidence sources** (§3): 10-Q/10-K full-document text extraction, investor-presentation/PDF ingestion, earnings-call transcripts, franchise-disclosure-document ingestion — all deferred pending a named paid source or provider for the ones that need one.
2. **`ecosystem_export.py` has no dedicated CLI test against the real production graph** — all 14 tests use synthetic graph fixtures for speed and safety. Worth a one-time manual spot-check of a real `--shareable` export before handing one to someone outside RB.
3. **`ecosystem_brief.py`** (the daily-brief section builder that already consumes `ecosystem_intelligence.json`) has no dedicated test file — flagged in `system/STATUS.md` but not addressed in this build; out of this request's scope.

Both originally-open items (workbook migration confirm, 10-Q/10-K scraper) are now done.
