# RB 9.86: Strategic Account Mapping (RB-DEFECT-041 Enhancement #3)

**Status:** Implemented (2026-06-15)
**Source:** `CLAUDE_DEFECT_RB_041_LINKEDIN_INTELLIGENCE_INGESTION_AND_RELATIONSHIP_ACTION_ENGINE.md`,
Enhancement #3 (lines 102-112), deferred during RB 9.73 triage as "RB 9.73b".

## Scope

RB-DEFECT-041 Enhancement #3 says RB should "automatically update account maps
when LinkedIn changes occur" for three account groups:

- **Global Payments / Worldpay** — new connections, shared relationships,
  internal influencers.
- **Foods Connected** — supply chain leaders, McDonald's stakeholders, industry
  connections.
- **Restaurant technology ecosystem** — PAR, Toast, Olo, Qu, NCR Voyix, Oracle
  Hospitality, Agilysys, Restaurant365, Crunchtime, Paytronix, DoorDash, Uber
  Eats, Shift4, Global Payments, Worldpay.

RB 9.73 found this "not built" and deferred it as a focused, self-contained
enhancement to `linkedin_ingest.py` — no schema migration required (unlike
Enhancement #4, which remains deferred).

## What was implemented

All changes are in `system/scripts/linkedin_ingest.py`:

- **`STRATEGIC_ACCOUNTS`** — a dict of account name → compiled regex, covering
  Global Payments/Worldpay, Foods Connected, and the restaurant-tech ecosystem
  vendors named in the defect. "Qu" was dropped from the initial list: as a
  bare 2-letter word-boundary pattern (`\bqu\b`) it produced false positives
  against plausible company names like "Qu Bistro Holdings" — the same class
  of issue RB 9.71 hit with short generic words in `_name_in()`. The other 14
  accounts all have multi-word or distinctive names and don't share that risk.
- **`_strategic_account_match(text)`** — returns the matching account name for
  a company-field string, or `None`.
- **`_recommend_actions(delta)`** — rewritten so that:
  - `rc_moves` whose `new_company` matches a strategic account get an
    appended "Add to [Account] account map." note on top of the existing
    reconnect/congratulations action.
  - `lki_moves` whose `new_company` matches a strategic account get a new
    "high" priority action: "Add to [Account] account map; light-touch
    reconnect on the new role." (previously these moves, if not also
    restaurant-tech-adjacent, generated no action at all).
  - `new_highlights` (new connections with a leadership title or
    restaurant/hospitality/AI-adjacent role) whose `company` matches a
    strategic account get a "high" priority "Add to [Account] account map;
    review as a warm-entry candidate before the connection goes cold." action,
    instead of the default "medium" priority generic review action.
- **`_build_delta_intelligence(...)`** — new `account_map_signals` list
  (capped at 20), added to
  `delta_intelligence.strategic_relationship_changes`. Each entry has
  `person`, `account`, `company`, `role`, and `movement_type` (reuses the
  existing `_movement_type(move)` helper for moves; `"new_connection"` for new
  baseline entries). Built from both `rc_moves`/`lki_moves` (matched on
  `new_company`) and `new_entries` (matched on `current_company`).
- **`_render_markdown(rep)`** — new "### Account Map Signals" sub-section under
  "## Strategic Relationship Changes", rendered when `account_map_signals` is
  non-empty: `- **{person}** → {account} ({company} / {role}, {movement_type})`.

## Tests

Added to `system/tests/test_linkedin_ingest_delta_intelligence.py`:

- `test_strategic_account_match_matches_tracked_accounts` — direct unit
  coverage of `_strategic_account_match()` against representative company
  strings for each account family.
- `test_strategic_account_match_avoids_false_positives` — `None`/empty input,
  and confirms generic words ("Acme Software Inc", "GK Software") and the
  dropped "Qu Bistro Holdings" case don't match.
- `test_linkedin_ingest_flags_strategic_account_map_signals` — full
  `li.ingest()` run with a fixture connection at "DoorDash" as Director of
  Partnerships; asserts `account_map_signals` contains the expected entry,
  the markdown report renders "### Account Map Signals", and
  `recommended_actions` elevates the person to "high" priority with
  "Add to DoorDash account map" in the action text.

Full suite: `python3 -m pytest system/tests/ -q` — **2394 passed** (114.24s),
up from RB 9.85's 2391 (3 new tests, zero regressions).

## Deferred

- **Enhancement #4** (RB-DEFECT-041, `title_history`/`company_history` per
  contact) — still deferred; needs a `baseline.schema.json` change plus a
  migration, out of scope for this sprint. This is now the sole remaining
  RB 9.73b item.
- "Qu" (restaurant tech company) was named in the defect's account list but
  excluded from `STRATEGIC_ACCOUNTS` due to the false-positive risk of a
  bare 2-letter word-boundary regex. If/when needed, it should be matched
  against a more specific signal (e.g. `linkedin_url` slug or a longer
  qualified name) rather than the bare company-name field.
- Live GPT re-sync — carried from RB 9.81-9.85, unaffected by this sprint
  (no KB changes).
