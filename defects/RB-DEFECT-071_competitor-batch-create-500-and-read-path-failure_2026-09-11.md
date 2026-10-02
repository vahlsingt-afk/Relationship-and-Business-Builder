# RB-DEFECT-071 — Competitor batch creation returns HTTP 500 and leaves the live read path unavailable

**Captured for:** Claude  
**Date:** 2026-09-11  
**Status:** Open — diagnosis, recovery, and implementation fix required  
**Severity:** Critical  
**Area:** Competitor Intelligence, `createCompetitor`, `listCompetitors`, persistence integrity, bulk ingestion

## User requirement

Todd supplied the 2026 FSTEC Restaurant Technology Guide and explicitly requested:

> grab this as intelligence too - these are all of the vendors at FSTEC and should be on our competitor list

The source PDF was successfully ingested through the governed file-ingestion path. The subsequent competitor-list mutations failed comprehensively, and the live competitor read path is now also returning HTTP 500.

This is not a conversational failure. It is a live API availability and persistence-integrity defect.

## Source and successful ingestion receipt

- Source: `/Users/toddvahlsing/Downloads/FSTEC_BuyersGuide_2026 (1).pdf`
- PDF: 40 pages, created 2026-09-09, approximately 21.8 MB
- Extracted named FSTEC participants: 150
- `uploadAndIngestFile`: **HTTP 200**
- `ingestion_id` / `document_id`: `58647330914f37b2`
- Triage receipt: `TRG-2026-09-11-2346`
- Processing status: `success`

The ingestion receipt reported one successfully applied watchlist-signal mutation for Toast. This proves the file ingestion was accepted and processed before competitor creation began.

## Baseline competitor state before the failure

Immediately before the create batch, `listCompetitors` returned **HTTP 200** with 11 tracked competitors:

1. PAR Technology
2. Toast
3. Oracle Food & Beverage (Simphony / MICROS)
4. NCR Voyix
5. Qu
6. Nory
7. Restaurant365
8. Revel Systems
9. Mood Media
10. Shift4 Payments
11. Fiserv

Seven FSTEC participants were already represented in that list: PAR Technology, Toast, Oracle Food & Beverage, NCR Voyix, Qu, Nory, and Restaurant365. Genius for Restaurants / Global Payments was intentionally excluded from competitor creation because it is Todd's own platform/company, not a competitor.

That left 142 requested FSTEC vendors to add.

## Failure observed

The 142 missing names were submitted to the documented `createCompetitor` operation using eight concurrent workers. Every request returned:

```text
HTTP 500
Internal Server Error
```

Summary from the caller:

```json
{
  "requested": 142,
  "success": 0,
  "failed": 142
}
```

The failures were not limited to ambiguous or noncompetitive names. They included known restaurant-technology vendors already represented in `ecosystem_intelligence.json`, such as Square, Adyen, SpotOn, Agilysys, Olo, Paytronix, Hi Auto, Tillster, Sparkfly, Crunchtime, and SoundHound AI.

After the create batch completed, a new read-only call was made:

```bash
python3 system/scripts/rb_cli.py call listCompetitors '{}'
```

It returned:

```text
HTTP 500
Internal Server Error
```

The pre-batch `listCompetitors` call had returned HTTP 200. The post-batch read failure therefore represents a verified state transition during this workflow.

## Trust and integrity impact

RB's governing rule is that no mutation may be claimed without a real 2xx receipt. Accordingly, none of the 142 additions can be reported as successful.

However, HTTP 500 alone does not establish that no partial write occurred. The post-batch failure of `listCompetitors` means the system currently cannot answer any of these critical questions through its public contract:

- Did zero competitors get written?
- Did some competitor directories or JSON records get created before downstream sync failed?
- Did concurrent writers corrupt an index or shared JSON file?
- Is the read path failing because one partially written record is malformed?
- Can a retry safely proceed without creating duplicates or compounding corruption?

The system therefore has both an availability failure and an unresolved canonical-state integrity risk.

## Root-cause hypotheses to test

Do not accept these as findings until verified from logs and state.

1. **Non-atomic file-backed writes under concurrency.** `createCompetitor` may update a shared registry/index without locking, temp-file replacement, or transactional serialization. Eight concurrent requests could interleave writes or expose a partially written file to readers.
2. **Creation succeeds but initial sync fails.** The endpoint automatically runs an initial intelligence sync. A competitor shell may be written before sync raises, causing a 500 after a partial mutation.
3. **One malformed generated record poisons listing.** `listCompetitors` may deserialize every competitor record and fail globally when one record is incomplete or invalid.
4. **Unbounded or unsafe batch semantics.** The public API exposes only single-record creation, but gives no concurrency contract, bulk endpoint, idempotency key, or retry guidance.
5. **Error handling hides the actionable cause.** The client receives only `Internal Server Error`; there is no structured stage, competitor name, rollback status, or recovery instruction.

## Required immediate recovery work

1. Stop automatic retries until canonical state is inspected.
2. Inspect server traceback/error logs for the 2026-09-11 batch window.
3. Enumerate competitor directories and registry/index files directly.
4. Validate every record against the competitor schema.
5. Identify files modified during the failed batch and determine whether writes were partial, duplicated, or malformed.
6. Restore `listCompetitors` to a verified HTTP 200 without discarding valid pre-existing intelligence.
7. Return a reconciliation report listing:
   - pre-existing records preserved;
   - newly created valid records, if any;
   - invalid/partial records repaired or quarantined;
   - the exact safe retry set.

Do not blindly delete or regenerate the entire competitor store. Existing competitor profiles contain real notes, product-line declarations, battle-card points, and review history that must be preserved.

## Required durable fix

1. **Make creation atomic and concurrency-safe.** Use a lock or transactional store for shared indexes, write records through temp-file-plus-atomic-replace semantics, and never expose partially initialized records.
2. **Separate or compensate the initial sync.** Either commit the competitor shell and return a successful creation receipt with a structured sync status, or roll back the shell if creation is contractually all-or-nothing. A downstream sync failure must not create an ambiguous mutation.
3. **Make list resilient.** One malformed record should be isolated and surfaced as a structured degraded-state item; it should not take down the entire competitor list.
4. **Add idempotency.** Re-submitting a normalized existing vendor should return a deterministic existing-record response, not duplicate or fail ambiguously.
5. **Add a governed bulk operation.** Accept a reviewed vendor roster, normalize aliases against the ecosystem graph, preview create/skip/reject decisions, then commit with per-item receipts and bounded server-side concurrency.
6. **Return structured errors.** Include stage (`normalize`, `create_shell`, `index_write`, `initial_sync`, `read_validate`), normalized slug, mutation status, rollback status, and safe retry guidance.
7. **Clarify taxonomy.** Todd uses the competitor list as the actionable FSTEC vendor universe, while the current tool description says it is only for vendors that directly compete with Genius. Either support a broader tracked-vendor/watchlist classification or make the UI/API distinguish direct competitors from adjacent ecosystem vendors without losing the user's requested coverage.

## Acceptance criteria

- `listCompetitors` returns HTTP 200 and preserves all 11 verified pre-existing competitors and their intelligence.
- Direct inspection and API reconciliation prove whether any of the 142 failed requests wrote canonical state.
- A failed initial sync cannot leave an ambiguous or malformed competitor record.
- Eight concurrent creates for distinct valid vendors do not corrupt shared state or break reads.
- Repeating the same create request is idempotent and produces a structured existing-record result.
- A malformed record is quarantined or reported without causing a global HTTP 500 from `listCompetitors`.
- A bulk import of the FSTEC roster returns a per-name result (`created`, `already_exists`, `rejected_with_reason`, or `failed_and_rolled_back`) plus aggregate counts.
- The post-import competitor/vendor tracking state can be read back and reconciled against the 150-name source roster.
- Genius/Global Payments is not mislabeled as its own competitor.
- All non-2xx responses include enough structured detail to diagnose and safely retry.

## Regression tests requested

1. `test_create_competitor_is_atomic_when_initial_sync_fails`
2. `test_concurrent_competitor_creates_do_not_corrupt_registry`
3. `test_list_competitors_survives_one_invalid_record`
4. `test_create_competitor_is_idempotent_by_normalized_vendor_identity`
5. `test_create_competitor_returns_structured_stage_error`
6. `test_bulk_competitor_import_returns_per_item_receipts`
7. `test_bulk_import_preview_normalizes_ecosystem_vendor_aliases`
8. `test_bulk_import_excludes_own_company_from_competitors`
9. `test_failed_batch_can_be_reconciled_to_exact_retry_set`
10. `test_preexisting_competitor_intelligence_survives_recovery`

## Files likely involved

- `system/api/server.py` (`createCompetitor`, `listCompetitors`)
- `system/scripts/rbb_chat_tools.py`
- `system/scripts/competitor_intelligence_common.py`
- `system/scripts/competitor_intelligence.py`
- `system/scripts/slug_safety.py`
- `system/competitor_intelligence/competitors/`
- `system/ecosystem_intelligence.json`
- competitor-intelligence API and persistence tests

## Priority guidance for Claude

Treat restoration and state reconciliation as the first priority. Do not simply restart the API and retry 142 writes. First prove the canonical competitor store is intact, identify whether the failed 500 requests partially mutated it, and restore a readable state. Then implement atomic, idempotent, concurrency-safe creation and a governed bulk-import path before retrying the FSTEC roster.
