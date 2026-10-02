# RB-DEFECT-073 — Deep-research competitor platform findings enter capture intake but do not reliably populate canonical RBB profiles

**Captured for:** Claude  
**Date:** 2026-09-28  
**Status:** Open — implementation and end-to-end verification required  
**Severity:** High  
**Area:** Deep Research, competitor intelligence, Genius capability intelligence, morning pipeline, review-first persistence

## User requirement

Todd requested a continuing deep-research cycle across every named restaurant-technology vendor in RBB, including Global Payments and the Genius product portfolio. For every vendor and every material restaurant-facing platform, the cycle must capture:

- the vendor's own marketing value proposition;
- independently supported strengths;
- marketplace-reported shortcomings and recurring complaints;
- implementation, support, integration, reliability, pricing, and usability limitations;
- product lineage, acquisitions, rebrands, customer/deployment scope, conflicts, confidence, and source provenance.

Todd then asked whether that research would automatically import into RBB. The verified answer is only partially: Markdown packets placed in `system/inbox/chatgpt_intelligence_drop/` are automatically captured and processed, but the structured company/platform findings do not have one automatic, schema-compatible, review-first path into the canonical competitor and Genius stores.

The required outcome is not merely “the packet was processed.” The relevant sourced findings must become queryable in the correct canonical RBB profile, with durable mutation receipts and review gates only where the new evidence conflicts with or would overwrite existing canonical judgment.

## Current behavior and evidence

### 1. Deep-research packets are automatically captured

The scheduled research cycle writes Markdown packets and JSON sidecars under:

```text
system/inbox/chatgpt_intelligence_drop/
```

The morning pipeline runs:

- `capture_ingest.py --scan`;
- `process_pending_captures.py`;
- `deep_research_coverage.py sweep-sidecars`;
- `competitor_intelligence_review.py scan`.

This proves that packets enter RBB's intake and reporting surfaces. It does not prove that every product, strength, weakness, or marketplace shortcoming becomes a canonical structured finding.

### 2. The current JSON sidecar is coverage metadata, not an import payload

`system/templates/deep_research_intelligence_drop.md` defines a sidecar containing:

```json
{
  "packet_id": "...",
  "targets": ["competitor:<slug>"],
  "pages_reviewed": 0,
  "candidate_pages_validated": 0,
  "conflicts_found": 0,
  "source_ledger": []
}
```

That sidecar is sufficient for coverage and source-quality accounting, but contains no structured per-vendor, per-platform findings that a canonical importer can apply.

### 3. The vendor extended-profile importer is manual and intentionally incomplete

`export_vendor_extended_profile_gaps.py` and `vendor_extended_profile_ingest.py` form a separate export/research/import flow. The importer accepts only:

```text
products, strengths, key_customers, recent_news, trends
```

It is not wired to the ChatGPT Intelligence Drop or to `morning_pipeline.py`. The accompanying instructions explicitly exclude:

```text
weaknesses, vulnerabilities, todds_pov, positioning_summary, vs_genius
```

However, the underlying competitor schema and writer already support sourced list entries for `weaknesses` and `vulnerabilities` through `competitor_intelligence.add_extended_profile_finding()`. The blanket exclusion therefore conflates two different classes of data:

1. externally sourced marketplace weaknesses/vulnerabilities, which can be evidence-backed facts or reported observations; and
2. Todd-authored competitive judgment (`todds_pov`, synthesized positioning, and unsupported “vs. Genius” conclusions), which must remain protected.

### 4. A separate Genius/competitive importer exists, but is not connected

`import_genius_competitive_research.py` can import a special `rb.competitive_research.v1/v2` JSON export. It already distinguishes:

- Genius capabilities and Genius evidence;
- competitor evidence notes;
- structured strength/weakness gap points;
- inference and confidence thresholds;
- idempotency through an import log.

But this is a manual CLI workflow for a different input schema. The current recurring research automation produces the standard Markdown packet plus coverage sidecar, so this importer is never automatically invoked.

### 5. Completion can currently be declared before canonical ingestion succeeds

The research cycle's coverage ledger can mark a target covered after validating a packet. No contract presently requires a successful canonical-import receipt before the dedicated platform-research cycle marks that competitor complete.

This permits the system to report “all competitors researched” while Team Portal and RBB query surfaces still show empty or partial Products, Strengths, Weaknesses, Vulnerabilities, and Genius evidence.

## User-visible impact

- Research exists in Markdown but is not reliably queryable as structured competitor intelligence.
- Team Portal can continue to display “None on file yet” after a successful research packet.
- Marketplace shortcomings may appear only as generic capture prose or a review suggestion.
- Global Payments/Genius research may be incorrectly routed through a competitor profile instead of Genius's own capability/evidence stores.
- The same evidence may be researched repeatedly because coverage state and canonical-field coverage are disconnected.
- A “complete” research cycle does not prove that RBB was materially enriched.

## Root cause

The architecture has three independently useful but disconnected contracts:

1. **Capture contract:** Markdown deep-research packets are swept and processed as captures.
2. **Coverage contract:** minimal JSON sidecars record targets and page outcomes.
3. **Canonical-import contracts:** vendor extended-profile and Genius competitive importers expect richer, different JSON schemas and currently require manual invocation.

There is no adapter that converts a validated deep-research packet into a canonical import proposal/apply transaction, no shared per-platform finding schema, and no end-to-end receipt tying research completion to durable RBB mutations.

## Required durable fix

### 1. Define one structured competitor-platform research payload

Extend the deep-research JSON sidecar or add a versioned companion result file. Preserve the existing coverage fields, and add structured records such as:

```json
{
  "schema": "rb.competitor_platform_research.v1",
  "packet_id": "dr-...",
  "targets": ["competitor:toast"],
  "records": [
    {
      "competitor_slug": "toast",
      "platforms": [
        {
          "name": "Toast POS",
          "category": "point_of_sale",
          "vendor_value_propositions": [],
          "strengths": [],
          "weaknesses": [],
          "vulnerabilities": [],
          "key_customers": [],
          "product_lineage": [],
          "marketplace_signals": []
        }
      ]
    }
  ]
}
```

Every finding must carry at least:

- `value`;
- `finding_type`;
- `source_url`;
- `source_owner` or publisher;
- `source_type`;
- `observed_at`/accessed date;
- `published_at` when known;
- `confidence`;
- `deployment_scope` when relevant;
- `is_vendor_claim`;
- `is_inference`;
- `limitations_or_conflicts`;
- platform/product identity.

Do not make free-text Markdown parsing the primary persistence contract. Markdown remains the human-readable evidence packet; the versioned JSON is the machine contract.

### 2. Build a validated, idempotent importer

Implement a dedicated importer or generalize the existing importers so the new payload routes findings to the correct existing writer:

- competitor products, sourced strengths, sourced weaknesses, sourced vulnerabilities, key customers, news, and trends -> `competitor_intelligence` extended-profile/evidence writers;
- independent competitive claims -> `add_competitive_note()` with evidence category and provenance;
- qualifying, evidence-backed strength/weakness comparisons -> the existing structured gap-point path, preserving the grounding evidence ID;
- Genius product capabilities -> `genius_capabilities` capability writer only when the claim is capability-shaped and meets the established confidence/inference gate;
- Genius weaknesses, complaints, financial/market context, customer signals, and lower-confidence claims -> Genius evidence log, not capability claims;
- Global Payments parent-level findings -> parent-scope Genius evidence, not a self-competitor profile.

Identity resolution must use exact live `competitor_slug`, explicit Genius product-line mappings, and existing alias resolution. Never guess a slug or auto-create an entity from ambiguous research output.

### 3. Preserve review-first semantics without blocking net-new evidence

Apply the repository's existing canonical mutation policy:

- net-new list finding with valid provenance -> auto-add;
- exact duplicate -> dedupe;
- newer dated observation -> append and preserve history;
- older dated observation -> append as historical evidence without regressing current state;
- scalar overwrite or undated contradiction -> queue for confirmation;
- ambiguous identity, product mapping, or scope -> queue for review with a specific reason;
- Todd-authored fields (`todds_pov`, unsupported positioning conclusions, final sales strategy) -> never auto-write from external research.

Externally reported weaknesses are not automatically Todd's opinion. Store them as sourced observations with publisher, confidence, scope, and limitations. The UI must label vendor claims, marketplace reports, and Todd's POV separately.

### 4. Wire import into the morning pipeline

After `capture_process_all` and sidecar validation, add a bounded import step that:

1. discovers new validated `rb.competitor_platform_research.v1` files;
2. runs schema and identity validation;
3. produces a dry-run/reconciliation plan internally;
4. applies allowed net-new mutations;
5. queues conflicts/overwrites/ambiguities;
6. records a durable per-finding receipt;
7. is idempotent across retries;
8. fails one packet or competitor without blocking unrelated packets.

The pipeline must not silently treat a malformed or incomplete result as success.

### 5. Tie cycle completion to import receipts

The dedicated state file `system/research/competitor_platform_cycle_2026-09-28.json` must distinguish:

```text
pending_research
packet_validated
import_applied
import_partial_review_required
blocked
complete
```

A competitor becomes `complete` only when:

- the qualifying platform research packet exists;
- the structured payload passes validation;
- every finding is accounted for as applied, deduped, queued with a specific review reason, or explicitly rejected with cause;
- the canonical read path verifies expected fields/evidence are visible;
- the receipt names the affected artifacts and counts.

### 6. Reconcile existing research rather than discarding it

Create a one-time migration/audit for:

- existing deep-research packets in `system/inbox/chatgpt_intelligence_drop/`;
- existing `latest_research_pack_status` / `gap_audit` competitor research;
- prior `rb.competitive_research.v1/v2` imports;
- already populated competitor extended-profile fields.

Do not blindly re-import prose. Identify packets with structured or safely extractable findings, generate a preview, dedupe against existing evidence, and report what remains non-importable or requires human review.

## Non-goals and safety boundaries

- Do not auto-write `todds_pov` from public web research.
- Do not convert vendor marketing claims into verified strengths without independent support.
- Do not synthesize “Genius wins because...” as canonical fact unless the established gap-analysis rules and evidence thresholds are met.
- Do not overwrite an existing scalar or resolve an undated conflict automatically.
- Do not mark a competitor complete merely because a Markdown file exists.
- Do not bypass the existing capture and review audit trails.
- Do not create new competitor records from fuzzy or ambiguous names.

## Acceptance criteria

1. A valid research result for a tracked competitor automatically enters the morning pipeline and populates its sourced `products`, `strengths`, `weaknesses`, `vulnerabilities`, `key_customers`, `recent_news`, and `trends` fields as applicable.
2. Vendor marketing statements are labeled as vendor claims and never silently promoted into independently verified strengths.
3. An independent, sourced marketplace shortcoming becomes a competitor weakness/evidence observation with confidence, date, URL, publisher, platform, and scope.
4. A recurring complaint supported by multiple independent sources preserves all source links or an evidence bundle, not a source-free synthesis.
5. A conflicting scalar or undated contradiction is queued for confirmation and leaves the existing canonical value unchanged.
6. A net-new sourced list entry auto-applies and leaves a durable mutation receipt.
7. Exact reruns are idempotent: no duplicate profile entry, evidence note, proposal, or receipt.
8. One malformed competitor record is rejected with a structured error without blocking other records in the packet.
9. Global Payments parent findings route to parent-scope Genius evidence; Genius platform capabilities and limitations route to the correct product scope; neither is mislabeled as an external competitor.
10. The Team Portal Competitor Snapshot and RBB API read paths show the newly imported fields after the same morning cycle.
11. Research-cycle completion is impossible until every finding in the qualifying packet has an import disposition and the canonical readback succeeds.
12. Existing historical packets can be audited and reconciled without duplicate mutations.
13. The morning pipeline reports counts that reconcile exactly: findings received, valid, applied, deduped, queued, rejected, competitors completed, and artifacts changed.
14. All writes use existing atomic persistence helpers and preserve prior competitor/Genius intelligence.

## Regression tests requested

1. `test_competitor_platform_sidecar_schema_requires_per_finding_provenance`
2. `test_vendor_claim_is_not_promoted_as_independent_strength`
3. `test_sourced_marketplace_shortcoming_populates_competitor_weakness`
4. `test_sourced_operational_risk_populates_vulnerability_with_scope`
5. `test_net_new_profile_finding_auto_applies_with_receipt`
6. `test_conflicting_scalar_routes_to_review_without_overwrite`
7. `test_import_is_idempotent_across_morning_pipeline_retries`
8. `test_one_invalid_record_does_not_block_other_competitors`
9. `test_global_payments_parent_claim_routes_to_genius_parent_evidence`
10. `test_genius_product_capability_and_weakness_route_to_distinct_stores`
11. `test_cycle_not_complete_until_import_dispositions_and_readback_exist`
12. `test_team_portal_reads_imported_products_strengths_and_weaknesses`
13. `test_existing_packet_reconciliation_dedupes_prior_imports`
14. `test_import_receipt_counts_reconcile_to_artifact_mutations`

## Files likely involved

- `system/templates/deep_research_intelligence_drop.md`
- `system/prompts/chatgpt_deep_research_cycle.md`
- `system/scripts/deep_research_coverage.py`
- `system/scripts/capture_ingest.py`
- `system/scripts/process_pending_captures.py`
- `system/scripts/morning_pipeline.py`
- `system/scripts/export_vendor_extended_profile_gaps.py`
- `system/scripts/vendor_extended_profile_ingest.py`
- `system/scripts/import_genius_competitive_research.py`
- `system/scripts/competitor_intelligence.py`
- `system/scripts/competitor_intelligence_common.py`
- `system/scripts/competitor_intelligence_review.py`
- `system/scripts/genius_capabilities.py`
- `system/scripts/team_profile_submissions.py`
- `system/scripts/team_tech_stack.py`
- tests for all modules above

## Required implementation deliverable

Claude should treat this as an implementation task, not an architecture memo. Deliver:

1. root causes confirmed against the current code;
2. the versioned structured payload schema;
3. importer/adapter implementation;
4. morning-pipeline wiring;
5. dedicated-cycle state transition repair;
6. migration/reconciliation preview for prior packets;
7. focused unit and integration tests;
8. one representative end-to-end fixture proving research packet -> validated import -> canonical readback -> Team Portal visibility;
9. exact mutation and test receipts.

Do not declare this defect closed from successful packet capture alone. Closure requires a real end-to-end run in which sourced competitor and Genius platform findings become visible through RBB's canonical read paths with the required review and provenance behavior.
