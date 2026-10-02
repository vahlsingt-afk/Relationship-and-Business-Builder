# Defect: CoS cannot mutate existing RBB canonical Google Drive records

**Date:** 2026-08-27  
**Severity:** P0 for the RBB pre-response intelligence contract  
**Status:** Reproduced; canonical Drive mutation blocked  
**Audience:** Claude / RBB engineering

## Expected behavior

Before the CoS answers, user input should be assessed for durable intelligence and applicable canonical RBB records should be mutated automatically. For the Pollo Campero RFI/RFP input, this includes the account, opportunity, stakeholder/contact, evidence, source registry, actions, and current pursuit state.

The execution environment should expose either:

1. the Bridgepoint Ops mutation API, or
2. equivalent write authority over the existing canonical files in the RBB Google Drive hierarchy.

Writes must preserve canonical file identity so downstream references and automations continue to resolve the same file IDs.

## Actual behavior

The Google Drive connector can:

- discover and read the existing canonical RBB folder and files;
- copy/upload new source evidence into the existing `sources` folder; and
- prepare valid replacement JSON/JSONL payloads locally.

However, it cannot replace the contents of any existing canonical file. Every `files.update` upload fails with HTTP 403 and:

> The user has not granted the app 77377267392 write access to the file.

No callable Bridgepoint Ops mutation endpoint was exposed in this task to provide the intended canonical write path.

## Reproduction

Account folder: `pollo-campero` (`1Noj44vaZAjVqpa_jKYwdfFirYIVT9F5X`)

Attempt byte-preserving in-place updates through the Google Drive connector for:

| Canonical record | File ID | Result |
|---|---|---|
| `account.json` | `1d02DuFBc2cjPcgafHx0jTe7B172_Hxva` | 403 `appNotAuthorizedToFile` |
| `actions.json` | `1hx9JS7wBOkmM6HH8SEbFZxPit5dGjTAp` | 403 `appNotAuthorizedToFile` |
| `brand_profile.json` | `1ArPgmET7eiNJQxjmEz9oMmx-k-VEabiF` | 403 `appNotAuthorizedToFile` |
| `evidence.jsonl` | `14qeh5shJRi0uH_b1N1DKQ55bSgZpkc6E` | 403 `appNotAuthorizedToFile` |
| `source_index.json` | `1XEc-SAFY4rI9DjNQ2eE3C4SvxXqnbj_v` | 403 `appNotAuthorizedToFile` |

The same app successfully created new evidence files inside canonical folder `sources` (`185KcSPDBPnEzFioWQ8SZLT2LpmKLvUnJ`), demonstrating a split permission model: create is allowed, mutation of existing files is not.

## User-visible impact

- The CoS can appear to have persisted intelligence while canonical records remain stale.
- RFI/RFP facts, Katherine Urbina's stakeholder record, opportunity stage/deadline, actions, and source provenance do not become durable.
- A second task cannot reliably recover the prior intelligence from canonical state.
- Creating replacement files would fork canonical identity and could leave downstream automations reading stale file IDs.

## Prepared recovery payload

The complete Pollo Campero mutation has been prepared and locally validated. It includes:

- opportunity stage set to active RFP with response due 2026-09-04;
- procurement state, RFI baseline commitments, and RFP requirement deltas;
- Katherine Urbina as a buying influence/contact;
- updated qualification and open questions;
- actions `0015` through `0024`;
- source records `0011` through `0014`; and
- evidence records `0013` through `0016`.

Recovery generator: `intelligence/pollo-campero/mutate_rbb_drive_recovery.py`  
Prepared payload directory: `/private/tmp/rbb_pollo_drive/`

## Required fix

1. Expose the authenticated Bridgepoint Ops mutation API to the CoS task runtime, including its schema/tool contract, **or** grant the Google Drive app (`77377267392`) edit/update authority on existing canonical RBB files.
2. Add a pre-response capability gate that verifies canonical mutation authority before claiming persistence.
3. Make the mutation transaction explicit: assess input, determine affected entities, update canonical records, write a receipt, re-read and verify, then speak.
4. Fail closed when durable mutation is required but unavailable. Report `persistence_blocked` with the exact failed targets instead of silently creating only local artifacts.
5. After repair, replay the prepared Pollo Campero payload against the five canonical IDs above and verify the expected fields and record counts.

## Acceptance criteria

- Existing canonical file IDs are updated in place successfully.
- A fresh read shows the Pollo Campero RFP deadline, Katherine stakeholder data, four new evidence entries, four new source entries, and actions through `0024`.
- The mutation receipt is durably stored in the account's `logs` folder.
- A new task can retrieve the updated state without relying on this conversation or local task files.

