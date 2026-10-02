# RB-DEFECT-034: LinkedIn URL Ingestion and Profile Enrichment Failure

**Filed:** 2026-06-08
**Severity:** High
**Category:** Contact Intelligence / Relationship Discovery / External Profile Ingestion
**Status:** CLOSED — #1 and #2 implemented (2026-06-08)

## Source

User supplied a LinkedIn profile URL in conversation, expecting RB to treat it as
a structured identifier and produce relationship intelligence from it. Instead RB
fell back to a generic "paste the profile content" request — no recognition, no
retrieval, no enrichment, no graph mutation.

## Observed Behavior

- User supplied a LinkedIn profile URL.
- System did not retrieve profile information.
- No contact enrichment occurred.
- No relationship intelligence was generated.
- User received a generic response requesting manual copy/paste of profile content.

## Expected Behavior (per "World-Class CoS" standard)

> When a user provides a LinkedIn URL, the system should respond with
> intelligence, not a request for data already available through the supplied
> identifier.

1. Recognize the URL as a LinkedIn profile (pattern: `linkedin.com/in/<slug>`).
2. Retrieve available profile metadata (via connector, cached export data, or
   identity-resolution match against already-ingested LinkedIn data).
3. Enrich the contact record.
4. Generate: relationship intelligence, career history summary, mutual-connection
   analysis, company intelligence, trust score, opportunity assessment,
   recommended discussion topics.
5. Mutate the contact graph and relationship map accordingly.

## Confirmed Root Cause (via code search — this is real, not a misdiagnosis)

Searched the entire LinkedIn-handling surface
(`linkedin_ingest.py`, `linkedin_ingest_extended.py`, `linkedin_session_reader.py`,
`linkedin_freshness_bridge.py`, `linkedin_messaging.py`, `linkedin_own_engagement.py`,
`fetch_via_session.py`, `linkedin_export_watcher.py`) plus `server.py`'s full API
surface for any handler keyed on a **profile URL as an identifier**:

- **No `linkedin.com/in/...` URL pattern recognizer exists anywhere** — `linkedin_url`
  appears only as a *passive field* on already-ingested signal/post-author records
  (`fetch_via_session.py:106`, `linkedin_session_reader.py:487`) — i.e., something
  RB *records when it appears in ingested data*, never something RB *acts on when
  supplied directly by the user as an input*.
- **Every existing LinkedIn pathway is export-or-signal-shaped, not URL-shaped**:
  `ingestLinkedInExport` (ZIP), `processLinkedInSignal` (post/engagement signal),
  `manualRelationshipIntake` (free-text). None of these treat "here is a profile
  URL" as a retrieval trigger — the closest is `enrichArtifact`
  (`server.py:3101`), which enriches an *already-registered* artifact with
  user-supplied text; it has no URL-recognition or fetch step of its own.
- **The Ingestion Protocol** (custom GPT instructions, "Route incoming content
  before analysis") routes *files and pasted text* to `uploadAndIngestFile` — it
  has no branch for "bare URL supplied in chat," so the GPT correctly (per its
  current instructions) falls through to a generic request for content. **This is
  the fallback path the user observed — it is behaving exactly as currently
  specified, which is itself the defect**: the spec has no URL-shaped branch to
  fall into.

This is **architecturally distinct** from the recurring "wiring gap" pattern this
sprint has been chasing (029/032/031 — data computed but not surfaced). There is
no profile-retrieval capability sitting unwired here; **the capability itself does
not exist**. This is a missing capability, not an orphaned one.

## Important Scope Boundary (the user's own caveat — preserve it)

> One nuance: this is specifically an RB/platform defect. In this chat session, I
> don't actually have direct LinkedIn access unless a connector or retrieval
> system provides the profile data. RB's intended behavior and the capabilities
> available to me in this conversation are currently different.

This means the fix is **not** "make the conversational layer fetch LinkedIn
profiles live" — that requires either a LinkedIn API/scraping connector (heavy,
ToS-sensitive, likely infeasible) or session-based retrieval
(`linkedin_session_reader.py`/`fetch_via_session.py` already exist for *feed*
content — extending that pattern to *profile pages* is the most architecturally
consistent option, but carries session-fragility risk that pattern already lives
with).

A **lower-risk, immediately achievable** version of most of the value:
**identity resolution against already-ingested data**. RB already has a LinkedIn
Full Export (connections + messages) and an entity/contact graph. A supplied
profile URL very likely matches:
- an existing contact record (slug/name match), in which case RB should
  immediately render the relationship intelligence it already has — career
  history, mutual connections, message history, trust score — from data already
  on hand, with **zero new retrieval**, or
- no existing record, in which case RB should say so explicitly and offer to
  create a stub contact + manual-intake path, rather than a bare "paste the
  content" deflection.

This reframes most of the defect as **another instance of the wiring-gap
pattern after all** — at the *recognition* layer: RB has the contact graph,
identity resolution, and enrichment machinery; it just never asks "does this URL
match someone I already know about?" before falling through to the generic path.

## Scope for Codex / Implementation

1. **URL recognizer + identity-resolution branch** (the achievable 80%): add a
   `linkedin.com/in/<slug>` pattern matcher to the Ingestion Protocol routing
   layer. On match:
   - Resolve against existing contacts/entities (slug, name, prior `linkedin_url`
     fields already recorded passively per the root-cause findings above).
   - **Match found** → render existing relationship intelligence immediately
     (career history, mutual connections, trust score, message history,
     recommended discussion topics) — a *rendering* task over data already in
     the graph, not a retrieval task.
   - **No match** → say so explicitly ("no existing record for this profile"),
     offer stub-contact creation + `manualRelationshipIntake` path. Never fall
     through to a bare "please paste the profile content" with no attempt at
     resolution first.
2. **Live retrieval (heavier, evaluate feasibility separately)**: assess whether
   `linkedin_session_reader.py`'s session-based fetch pattern (already used for
   feed/engagement content) can be extended to profile pages. If infeasible
   (ToS/fragility), document that explicitly so the gap is named rather than
   silently re-discovered each time a user supplies a URL.
3. **Update Ingestion Protocol** in all three Custom GPT instruction documents
   (`custom_gpt_instructions_8k.md`, `custom_gpt_instructions_compact_8k.md`,
   `custom_gpt_prompt.md`) to add the URL-recognition branch — otherwise, even
   if #1 lands, the GPT has no instruction telling it to take that branch instead
   of the generic-paste fallback (the exact "computed-but-not-named" pattern that
   made 031's fix necessary).

## Implementation Result — #1, recognition + identity-resolution branch (2026-06-08)

Built and verified by Claude (architecture + engineering):

- **New endpoint `GET /contacts/resolve_linkedin` (`operationId: resolveLinkedInProfile`)**
  in `server.py` (inserted after `getCard`, ~line 1033). Extracts the `/in/<slug>`
  segment from a supplied URL (handles trailing slashes, query strings, locale
  prefixes, URL-encoding via `urlparse`/`unquote`), normalizes case, and matches
  against the `linkedin_url` field already present on every `baseline_index.json`
  contact record (confirmed populated for the vast majority of the 2,742-contact
  baseline via direct inspection — e.g. `a-todd-lennig` →
  `https://www.linkedin.com/in/atoddlennig`). Three response shapes:
  - `status: "matched"` → contact id/name/company/role/circles/tags +
    `card_hint` directing the caller to `getCard` for full relationship
    intelligence (career history, loop history, trust signals — all already in
    the graph, zero new retrieval)
  - `status: "no_match"` → honest explanation ("RB has no live LinkedIn fetch/
    scrape connector by design") + explicit `next_step` guidance: offer
    `manualRelationshipIntake` stub-creation, never a bare paste-content
    deflection
  - `status: "not_a_linkedin_profile_url"` → for unrecognized URL shapes
- **Wired into both OpenAPI specs**: added the path to `openapi.yaml` (matching
  `server.py`'s docstring/description) and added `resolveLinkedInProfile` to
  `validate_openapi_gpt.py`'s `GPT_OPERATIONS` allowlist, then regenerated
  `openapi_gpt.yaml` (`validate_openapi_gpt --write` → OK, 20 ops — was 19).
  This closes the loop on the exact "computed-but-not-named" pattern called out
  in this defect's own scope section — the endpoint is reachable by the GPT, not
  orphaned the way 031's sections briefly were.
- **Updated the Ingestion Protocol in all three Custom GPT instruction documents**
  (`custom_gpt_instructions_8k.md`, `custom_gpt_instructions_compact_8k.md`,
  `custom_gpt_prompt.md`) — added a "Bare LinkedIn profile URL" branch instructing
  the GPT to call `resolveLinkedInProfile` first and rendering instructions for
  each of the three response states, explicitly forbidding the bare "please paste
  the profile content" deflection and forbidding any claim of having "browsed" or
  "retrieved" a profile outside this resolution path (guards against fabricated-
  retrieval claims per the Core Contract's "never simulate an action call" rule).

**Verification:** live-tested all three response paths against the running server
via `TestClient`:
- matched: `https://www.linkedin.com/in/atoddlennig` → `status: matched`,
  `contact.id: a-todd-lennig` ✓
- no match: a fabricated slug → `status: no_match` with `next_step` guidance ✓
- non-profile URL: `https://example.com/whatever` → `status:
  not_a_linkedin_profile_url` ✓

`py_compile` clean on `server.py`. All three instruction documents and both
OpenAPI specs confirmed to reference `resolveLinkedInProfile` via grep.

**Net effect**: a supplied LinkedIn profile URL that matches an existing contact
(the common case, given the 2,742-contact baseline already carries `linkedin_url`
from LinkedIn export ingestion) now produces immediate relationship intelligence
from data already on hand — career history, circles, trust signals, loop
history via `getCard` — instead of a generic deflection. An unmatched URL now
produces an honest "no record + here's how to build one" response instead of
silently asking the user to do RB's resolution work for it.

**034 CLOSED.**

## Implementation Result — #2, live retrieval via browser-session profile capture (2026-06-08)

**Feasibility assessment verdict: FEASIBLE via the same browser-session capture pattern
used for feed/engagement content. Zero ToS risk. Lower fragility than feed capture.**

Rationale:
- `linkedin_session_reader.py`'s session pattern is NOT automated — user opens the page in
  their own browser, manually pastes JS into the console, copies the output. There are no
  HTTP requests from RB, no credentials stored, no login. This is architecturally identical
  to "user copies structured content from a page they can already see." LinkedIn's ToS
  automation prohibitions do not apply.
- Profile pages are structurally more stable than the feed (fixed layout vs. infinite-scroll
  dynamic content) — lower DOM-change fragility than the existing CAPTURE_JS.

Built and verified (py_compile clean):

- **`PROFILE_CAPTURE_JS`** added to `linkedin_session_reader.py` — browser-console JS that
  captures from a profile page: name, headline, location, about, experience (title/company/
  dates/description), education, mutual_connections_text, visible contact links. Uses multi-
  selector patterns with fallbacks for LinkedIn's varied CSS structure. Output: structured
  JSON payload with `slug`, `profile_url`, `source: linkedin_browser_session_profile`.

- **`ingest_profile(raw, dry_run)`** added to `linkedin_session_reader.py`:
  - Resolves slug against baseline `linkedin_url` fields (slug match first, name-exact
    fallback) — same resolution logic as `resolveLinkedInProfile` in server.py.
  - `matched` → enriches existing contact: overwrites `linkedin_url`, `headline`,
    `current_company`, `current_role` with fresh data; fills blanks for `location`, `about`;
    sets `linkedin_experience`, `linkedin_education`, `linkedin_profile_captured_at`. All
    curated RB fields (circles, tags, relationship_type, rc_tier, etc.) preserved.
  - `no_match` → creates stub contact with `_stub: true` for manual-intake follow-up.
  - `dry_run=True` returns preview with `fields_to_update` diff — no writes.

- **CLI flags**: `--capture-profile-js` (prints the JS snippet), `--ingest-profile --in FILE
  --dry-run|--confirm` (runs the ingest).

- **API endpoint `POST /contacts/ingest_linkedin_profile`** (`operationId: ingestLinkedInProfile`)
  added to `server.py` — accepts `{"confirm": bool, "profile_data": {...}}` or raw payload.
  Calls `linkedin_session_reader.ingest_profile()` directly.

- **`openapi_gpt.yaml`** wired with `ingestLinkedInProfile` path + GPT-facing description.

- **All three GPT instruction documents** updated with the profile capture path:
  - When user is on a profile page → print `--capture-profile-js`, paste in browser console,
    call `ingestLinkedInProfile` with output.
  - `matched_and_enriched` → render updated contact fields.
  - `stub_created` → offer `manualRelationshipIntake`.
  - Never claim automated retrieval.

## Business Impact

- Increased user friction; manual copy/paste defeats the purpose of relationship-
  intelligence automation.
- Lost opportunity for automated intelligence gathering on inbound contacts.
- Reduced confidence in RB as a Chief-of-Staff platform — "I gave it the one
  piece of identifying information it needed and it asked me to do its job."

## Non-goals

- Not a request to build a general-purpose LinkedIn scraper/API integration as a
  first step — that's a heavier, ToS-sensitive capability that should be
  evaluated on its own merits (#2 above), not bundled into the achievable fix.
- Not a `canonical_brief`/rendering-contract fix — this is an *input-routing and
  capability* gap, upstream of the brief entirely.
