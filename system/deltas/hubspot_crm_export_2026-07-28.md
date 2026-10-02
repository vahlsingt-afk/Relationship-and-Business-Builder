# HubSpot CRM Export Ingest — 2026-07-28

**Source tag:** `hubspot_crm_export_2026-07-28`

## Mutation Report

- Knowledge Sources Updated: 1
- People Imported: 338
- Existing People Updated: 0
- New People Created: 0
- Duplicate Candidates: 0
- Companies Added: 0
- Relationship Links Created: not computed
- Knowledge Mutations Applied: 0
- Confidence: 70.0%

- Relationship Links Created not computed — a HubSpot contacts CSV carries no Industry/Event/Opportunity/Meeting data to link (RB-DEFECT-064 Stage 5, still open).

## Intelligence Generated

- No dormant relationships among the contacts this import touched.

- No warm-intro broker found for any newly created contact's company.

## What the system did NOT do

- Did not overwrite any existing canonical field silently (conflicts logged instead).
- Did not create Person->Industry/Event/Opportunity/Meeting edges — this CSV has no such data (RB-DEFECT-064 Stage 5, still open).
