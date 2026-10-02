# RI / Relationship Overlay Design

**Sprint:** RB 9.14 — Ecosystem Vendor Evidence and Verification  
**Status:** Design artifact — implementation follows in a later sprint once vendor edges exist at scale  
**Depends on:** `system/ecosystem_intelligence.json` vendor relationship edges being populated

---

## Purpose

This document defines how RB will connect the ecosystem intelligence graph to the user's real-world relationship signals — email, calendar, LinkedIn, CRM contacts, and active threads. The goal is to give the ecosystem graph a *coverage* dimension: for any brand or vendor entity, RB should know whether Todd has a direct contact, a one-hop connection, an active thread, or no coverage at all.

This connects two existing systems:
- **Ecosystem graph** — `ecosystem_intelligence.json`: brand/vendor entities, relationships, signals, assessments
- **Relationship intelligence (RI)** — profiles, active threads, contacts, and the `user_relevance` section of the graph (currently empty)

---

## Core Concept: Entity Coverage Score

For any entity in the graph (brand or vendor), RB should be able to answer:

> "Does Todd have a direct relationship, an introduction path, or an active thread with anyone at this company?"

The answer populates `user_relevance` records in the graph and is used by:
- `query-brand` (already returns `user_relevance` array)
- `query-vendor` (`relationship_coverage` field)
- Daily brief surfacing ("who matters now")
- Strategic recommendations

---

## Linkage Model

### 1. Brand entity → contacts by current company

**Mechanism:** Match brand entity name against the `current_company` field in RB contact profiles.

**Example:**
- Entity: `brand-mcdonalds` (name: "McDonald's")
- Profile scan: find all contacts whose `current_company` contains "McDonald" or "McDonald's"
- Result: create `user_relevance` record linking the entity to those contact IDs

**Coverage status mapping:**
- Direct contact at the brand → `direct`
- Contact who previously worked there → `one_hop` (via shared history)
- Contact who knows a contact there → `two_hop`
- No match → `none`

**Implementation note:** Match should be fuzzy (normalize both sides with `_slug()`) and verified against the contact's current company, not just any mention. Former employees are tracked separately as `one_hop` historical coverage.

### 2. Vendor entity → contacts by current company

Identical mechanism to brand entity coverage, applied to the `to_entity_id` (vendor) side of relationships.

**Example:**
- Entity: `vendor-par-technology`
- Profile scan: find contacts at PAR Technology
- Coverage record attached to the vendor entity, queryable via `query-brand` on any brand that uses PAR

**Strategic value:** When RB surfaces Burger King's PAR Brink rollout, it should also surface whether Todd knows anyone at PAR who could provide access or context.

### 3. Active threads → ecosystem entity IDs

**Mechanism:** Match active thread subjects, participants, and body summaries against entity names in the graph.

- Thread participant's company → look up entity by company name
- Thread subject/content → scan for brand/vendor name mentions (exact + slug match)
- On match: create a signal record in the graph linking the entity to the thread ID

**Example:**
- Active thread with subject "Brink POS rollout Q3" and participants from PAR Technology
- → Signal attached to `vendor-par-technology` entity: `type: active_thread_signal`
- → `user_relevance` record on `vendor-par-technology` updated with thread reference

**Signal format (graph signal record):**
```json
{
  "id": "sig-2026-05-27-par-thread-q3",
  "event_at": "2026-05-27",
  "signal_type": "active_thread_signal",
  "summary": "Active email/calendar thread involving PAR Technology participant",
  "entities": ["vendor-par-technology", "brand-burger-king"],
  "sources": ["src-email-thread-ref"],
  "confidence": {"level": "high"},
  "interpretation": "Direct engagement signal. Todd has an active thread with PAR contacts."
}
```

**Privacy rule:** Thread content is not stored in the graph. Only the entity linkage and a data-only summary are persisted. No raw email prose enters the graph.

### 4. Franchisee groups → brand entities

**Mechanism:** Franchisee group contacts (operators, multi-unit owners, area developers) are linked to the brand they franchise from via `operates_units_for_brand` relationship type.

**Example:**
- Contact: "Regional operator, Burger King — 48 units, Southeast"
- → Entity: `operator-entity-id` (type: `operator`, subtype: `franchisee_group`)
- → Relationship: `operates_units_for_brand`, from_entity: operator, to_entity: `brand-burger-king`

**Why this matters:** Franchisee operators are often the actual technology decision-makers, not corporate. A franchisee contact at a large multi-unit Burger King group may have more direct POS influence than a corporate contact. The overlay should surface this connection when RB queries Burger King's vendor stack.

---

## `user_relevance` Record Schema

The existing schema already defines this. Here's the intended content for the overlay:

```json
{
  "id": "ur-brand-mcdonalds-direct",
  "entity_id": "brand-mcdonalds",
  "relevance_type": "contact_at_brand",
  "score": 85,
  "relationship_coverage": {
    "status": "direct",
    "people": ["profile-id-1", "profile-id-2"],
    "threads": ["thread-id-abc"],
    "notes": "Two direct contacts at McDonald's Corp. One active thread."
  },
  "rationale": "Todd has direct contacts at McDonald's. Coverage is strong.",
  "updated_at": "2026-05-27T00:00:00"
}
```

**Score guidance:**
- `direct` contact, active thread → 85–100
- `direct` contact, no thread → 60–80
- `one_hop` (former employee or mutual connection) → 30–55
- `two_hop` → 10–25
- `none` → 0

---

## Surfacing Contract

Once the overlay is implemented, the following queries become meaningful:

### Daily brief: "Who matters now"

For each strategic recommendation or assessment in the graph, look up `user_relevance`. Surface entities where:
- Coverage is `direct` or `one_hop` AND
- A recent signal exists (vendor transition, earnings, job posting, announcement)

Output: "McDonald's is transitioning digital loyalty infrastructure. Todd has a direct contact at McDonald's Corp — [name]."

### `query-brand` enhancement

The current `query-brand` already returns `user_relevance` as an array. Once populated, this becomes:
```
"user_relevance": [
  {
    "relevance_type": "contact_at_brand",
    "score": 85,
    "relationship_coverage": {"status": "direct", "people": [...], "threads": [...]}
  }
]
```

### `query-vendor` `relationship_coverage` field

Currently returns `"unknown"`. Once overlay is populated, returns the coverage status from `user_relevance` on the vendor entity.

---

## Implementation Sequence (future sprint)

1. **Build entity name → profile company matcher** in a new `overlay.py` script
   - Input: graph entities + RB profile directory
   - Output: `user_relevance` records written to the graph
   - Audit: `item_persisted` events for each coverage record written

2. **Build thread → entity matcher**
   - Input: active threads (from `active_threads.yaml` or email signals) + graph entity names
   - Output: graph `signals` records for thread linkages
   - Audit: `source_accessed` for thread content access; no raw content stored in graph

3. **Build franchisee group entity model**
   - Add `operator`-type entities for known multi-unit franchisee groups
   - Add `operates_units_for_brand` relationships

4. **Wire overlay refresh into daily brief pipeline**
   - After morning source access: refresh `user_relevance` records for entities mentioned in new signals
   - Surface coverage + signal combinations to Todd

---

## Privacy Constraints (from P-038)

- No raw email or calendar content enters the ecosystem graph
- Thread linkages are stored as data-only summaries and entity references
- Contact names stored in `user_relevance.people` are profile IDs, not raw names
- Any LLM summarization of thread content before storage must use `wrap_external_content()` from `privacy_guard.py`
- `user_relevance` records are classified as `intelligence` data class, retention class `derived_intelligence` (90 days)
- Contact ID references in `user_relevance.people` are classified as `memory` / `user_exportable`

---

## Open Questions (to resolve before implementation)

1. Should `user_relevance` records be rebuilt fresh on each overlay refresh, or patched incrementally?
2. How should RB handle contacts who have left a company — demote coverage score, keep as `one_hop`, or remove?
3. Should franchisee operator entities be auto-created from existing profile tags, or require manual seed?
4. What is the latency budget for overlay refresh — real-time on query, or batch during daily brief?
