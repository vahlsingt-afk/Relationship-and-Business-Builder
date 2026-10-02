# RB-DEFECT-028: Entity Alias & Ticker Fields for Intelligence Scope Expansion

**Filed:** 2026-06-07
**Status:** Implemented — 2026-06-07
**Author:** Claude (architecture)

## Problem

Intelligence gathering (news/market signal search, earnings monitoring, ecosystem
briefs) keys off a single canonical `entity_name` / `name` string per entity. Real
entities are referenced under multiple surface forms:

- Informal/punctuation variants: "Freddy's" vs "Freddys" vs "Freddy's Frozen Custard"
- Corporate vs product/brand naming in tech vendors: "Genius" vs "Xenial" vs "RTI"
- Stock ticker symbols used in financial press and earnings coverage: e.g. PAR
  Technology ↔ `PAR`

Searching only on the canonical name under-collects signal — coverage that uses an
alias or ticker is silently missed, narrowing the intelligence aperture.

## Current schema state (no change needed to structure — only to population + a new field)

[system/ecosystem_intelligence.json](../system/ecosystem_intelligence.json) already
defines an `aliases: []` array on every entity record (1,610 entities, confirmed via
inspection — all currently empty):

```json
{
  "id": "brand-mcdonald-s",
  "name": "McDonald's",
  "entity_type": "brand",
  "subtype": "restaurant_brand",
  "status": "active",
  "domains": ["restaurants"],
  "aliases": [],
  "attributes": { ... }
}
```

`aliases` is consumed today only by
[query_engine.py:165-171](../system/scripts/query_engine.py#L165) for inbound
entity-name resolution (matching a user's question text to an entity record). It is
**not** currently used to widen outbound search/signal-gathering queries.

## Design decisions

1. **No structural schema migration required for `aliases`.** The field exists and is
   correctly typed (`list[str]`). This is a **data backfill** task: populate name
   variants per entity (punctuation forms, legacy/DBA names, parent-vs-brand names for
   tech vendors).

2. **Add a new top-level `ticker` field**, sibling to `aliases` — not nested inside
   `attributes`. Rationale: `ticker` is an *identity* attribute consumed by
   query-construction logic (same tier as `name`/`aliases`), not a descriptive/
   measurement attribute like `system_sales` or `unit_count`. Keeping identity fields
   flat and parallel makes the query-builder's job a simple, uniform read:
   `entity.get("ticker")` alongside `entity.get("aliases")`, with no special-casing
   for nested lookups. Value is the public ticker symbol string, or `null` for
   privately held entities (the overwhelming majority — most restaurant brands and
   many tech vendors in this graph are private).

   ```json
   {
     "name": "PAR Technology",
     "aliases": ["PAR", "ParTech", "Brink POS", "PAR Punchh"],
     "ticker": "PAR",
     ...
   }
   ```

3. **Query construction must fan out over the full identity set, not just `name`.**
   Wherever a search/signal-gathering query string is assembled for an entity (news
   search, earnings monitor, market-signal ingestion), the term set should be:

   ```
   search_terms = [entity["name"]] + entity.get("aliases", []) + (
       [entity["ticker"]] if entity.get("ticker") else []
   )
   ```

   This is the actual fix for the scope-narrowing risk — the alias/ticker *data*
   alone changes nothing until the gathering layer consumes it. Backfill and
   query-builder change must land together (or the data sits inert, as `aliases`
   has for the life of the schema so far).

## Scope for Codex (backfill + wiring)

- **Backfill `aliases`** for all 1,610 entities in `ecosystem_intelligence.json`:
  punctuation/spacing variants, DBA/legacy names, and — for tech-vendor entities
  specifically — parent-company ↔ product-brand name pairs (e.g. Genius ↔ Xenial ↔
  RTI–style relationships, where they reflect real corporate/brand history).
- **Add `ticker`** (top-level, `null` default) for entities that are public companies
  or subsidiaries of public companies; leave `null` for private entities.
- **Update query-construction call sites** in the signal-gathering scripts (locate via
  `grep -rn "entity\[.name.\]\|entity_name" system/scripts/`) to fan out over
  `name + aliases + ticker` per the pattern above, rather than searching on `name`
  alone.

## Non-goals

- No change to the `aliases` field's type or location — it's already correctly
  positioned in the schema.
- No retroactive re-scoring of historical signal/intelligence records collected under
  the narrower query scope — this is a forward-looking aperture widening only.

## Implementation Result

- Added `system/scripts/entity_identity.py` as the shared identity-term and
  query-construction primitive.
- Added idempotent `system/scripts/backfill_entity_identities.py`.
- Processed all 1,610 entities:
  - 1,006 aliases across 696 entities with defensible alternate surface forms.
  - top-level `ticker` present on all entities.
  - 20 public-company or unambiguous public-parent ticker identities populated.
  - private or ambiguous entities remain `null`.
- Updated the external web scanner to match `name + aliases + ticker` and return
  the canonical entity name regardless of which surface form matched.
- Updated earnings monitoring to expose `search_terms` and fanned-out
  `search_queries`, and to deduplicate watch candidates across all identity terms.
- Updated ecosystem and signal indexes to consume ticker alongside aliases.
- Corrected Olo to `ticker: null` following completion of its September 12, 2025
  acquisition by Thoma Bravo.
- Validation:
  - ecosystem schema: pass
  - backfill idempotence: byte-for-byte stable
  - focused regression suite: 239 passed
