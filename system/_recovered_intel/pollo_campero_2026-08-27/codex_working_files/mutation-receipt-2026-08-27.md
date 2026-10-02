# Mutation Receipt - Pollo Campero RFI/RFP Recovery

## Event

- Mutation date: 2026-08-27
- Trigger: User instruction to persist and mutate the Pollo Campero RFI/RFP intelligence
- Result: Completed in local project workspace
- Mutation type: Recovery ingestion and canonical record creation

## Entities resolved

- Account: `account-pollo-campero-usa`
- Person: `person-katherine-celeste-urbina-barillas`
- Opportunity: `opportunity-pollo-campero-digital-menu-boards`

## State changes recorded

- Digital Menu Boards procurement advanced from RFI to RFP - Active Competitive Procurement.
- Response deadline recorded as 2026-09-04.
- Katherine Urbina recorded as primary procurement contact.
- RFI submission established as the commitment baseline.
- TDR and SOW established as governing RFP sources.
- Required, optional and net-new scope persisted.
- Integration, logistics, support, warranty and commercial risks persisted.
- Customer questions and internal open loops persisted.

## Files created

- `README.md`
- `account.md`
- `contact-katherine-urbina.md`
- `opportunity-digital-menu-boards.md`
- `requirements-delta.md`
- `response-workplan.md`
- `source-registry.md`
- This mutation receipt

## Evidence preserved

- Submitted RFI response PDF
- Customer TDR PDF
- Customer SOW/pricing workbook
- RFP transmittal email screenshot

## Original-file protection

No user-supplied original was edited, moved or deleted. Evidence files in this package are byte-for-byte copies verified by SHA-256 hashes in the source registry.

## Known limits

- No existing canonical RBB file schema was present in this project mirror, so a self-contained Markdown schema was created.
- No external RBB database or service was mutated because none was exposed in this task.
- Internal product, operations, support, legal and pricing approvals remain open; the records distinguish submitted claims from independently validated capability.

## Verification requirement

Future retrieval should open `README.md`, resolve the three canonical entity IDs, follow evidence links, and recover the deadline, opportunity stage, RFI commitments, RFP deltas and open loops without requiring the user to re-upload the documents.
