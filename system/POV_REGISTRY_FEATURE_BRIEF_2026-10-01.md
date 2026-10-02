# User POV Registry — Feature Brief

**Status: received 2026-10-01, queued — 4th item in queue, not started.**
Queue order as of this date: (1) the original feature request from earlier
this session, (2) Technology Lifecycle Phase 1 (importer, API operations,
Team Portal surface, brief enrichment — see `system/technology_lifecycle/`),
(3) Overwrite Confirmation Queue + weekly review spreadsheet (plan saved at
`~/.claude/plans/inherited-booping-sparkle.md`), (4) this.

## Why this is a real gap, not a nice-to-have

Confirmed against the live repo on 2026-10-01:

- `system/00_TODD_PROFILE.md` exists and is exactly the problem described:
  a single static, unversioned file mixing biography, preferences,
  strategic theses, and hard operating boundaries with no way to track
  when a belief changed, why, or what evidence supports/challenges it.
- `system/CANONICAL_REGISTRY.yaml`'s `strategic_theses` domain already
  documents this as an open gap in its own schema: `authority_status:
  distributed_consolidation_pending`, `authoritative_store: null`, with
  four different `current_stores` (`active_threads.yaml`,
  `strategic_events.json`, `account_intelligence/`, `research/`) and two
  more `projections` (`briefs/`, `published/`) — i.e. the registry itself
  already flags that no single store owns this today.

## Naming: User POV Registry, not Todd's POV Registry

Renamed 2026-10-01 (Todd's own instruction) from the original proposal's
"Todd's POV Registry" to **User POV Registry** — this is designed as a
general RBB capability that could be monetized/offered to other users
down the line, so the name (and the canonical domain) should be
user-agnostic from the start rather than renamed later as a breaking
change. This matches `ARCHITECTURE.md`'s existing design principle
verbatim: *"Configurable core, user-specific layer... the engine should
stay user-agnostic and industry-agnostic. User identity, communication
style, industry context, goals, terminology, integrations, and
preferences belong in a replaceable profile/configuration layer."* Todd
is the first (and for now only) user whose POV populates it; the registry
itself, its schema, and its operations should never bake his name in.

Also renaming the canonical domain from the original proposal's
`operator_pov` to **`user_pov`**: "operator" already has an established,
different meaning throughout this codebase (a restaurant franchisee/
operator — e.g. GPS Hospitality) and reusing it here for "the RBB user"
would collide with that vocabulary in confusing ways, especially once
franchisee/operator-level technology intelligence (see the Technology
Lifecycle and FDD features already queued) and this registry exist
side by side.

## The proposal

**Two-layer design**, under the canonical domain name `user_pov`
(user-facing name: **User POV Registry**) — deliberately named to keep
personal perspective separate from objective intelligence (ecosystem
graph) and system doctrine (ARCHITECTURE.md/SCHEMAS.md rules):

1. **User POV Registry** — atomic, governed beliefs and principles.
2. **POV Frameworks** — longer authored documents (e.g. a restaurant-
   technology economics framework), linked to the atomic entries their
   sections produce.

### Atomic POV entry fields

- Exact statement
- `type`: `principle | hypothesis | evaluative_lens | metric |
  research_question | hard_boundary`
- `scope`: global, restaurant technology, AI, franchise economics,
  enterprise sales, vendor LTV, relationships, etc.
- `status`: `active | testing | qualified | superseded | retired`
- `conviction`: working hypothesis through foundational principle
- User-authored vs. RBB-inferred
- Source document and section
- Created / last-reviewed dates
- Supporting, challenging, and qualifying evidence
- Supersession history
- Which RBB surfaces should apply it

### The critical separation

| Layer | Meaning |
|---|---|
| User's POV | What the user believes or wants used as an evaluative lens |
| Evidence | Independent facts that support, challenge, or qualify it |
| RBB doctrine | Rules controlling how the system behaves |
| Research questions | Things RBB should investigate without presuming the answer |

### Worked example

For an authored framework document (e.g. the restaurant-tech-economics-
lifecycle-AI document), the full text stays an authored framework; its
sections produce reviewable atomic entries, e.g.:

- "Technology should be judged by restaurant-level outcomes" → evaluative
  lens
- "Restaurants may be spending more without proportional profit
  improvement" → testing hypothesis
- "Incomplete stacks must not be called inexpensive" → research rule
- "AI must clear a higher economic bar" → evaluative principle
- "Award does not equal deployment" → evidence-quality principle
- "Customer LTV extracted versus delivered" → vendor-economics lens

### Recommended canonical structure

```
system/pov/
  events.jsonl                 # Immutable history
  registry.json                # Current canonical projection
  frameworks/
    restaurant-tech-economics-lifecycle-ai.md
  evidence_links.jsonl         # Supports, challenges, qualifies
system/schemas/
  pov.schema.json
```

### Recommended live operations

`listPOVEntries`, `getPOVEntry`, `getPOVFramework`, `addPOVEntry`,
`revisePOVEntry`, `retirePOVEntry`, `attachPOVEvidence`,
`importPOVFramework`, `reviewPOVFrameworkExtraction`.

### Write discipline

Additive by default. RBB must never silently rewrite or weaken a user's
POV based on an external article — external evidence only supports,
challenges, or qualifies an existing entry, never overwrites it (this is
the same "no silent overwrite" principle behind the Overwrite Confirmation
Queue feature already queued at position 3 — the two should be designed
to share that discipline, not duplicate it). A derived entry extracted
from an imported document stays `proposed` until reviewed; the user's own
verbatim declarations can be promoted immediately.

## Notes for whoever picks this up

- Check `system/active_threads.yaml`, `system/strategic_events.json`,
  `system/account_intelligence/`, and `system/research/` for what's
  already there before designing the migration — the registry's own
  `current_stores` list names these as the pre-existing, scattered
  sources of truth this consolidates. `system/strategic_events.py` is
  listed as the current `mutation_owner` for `strategic_theses` and
  should be read first; this new registry likely supersedes or wraps it
  rather than sitting beside it unrelated.
- `system/00_TODD_PROFILE.md`'s existing content should be the seed
  migration source — read it in full and propose a first-pass set of
  atomic entries from it as part of scoping this, rather than starting
  from zero.
- This shares a design principle (additive-only, human confirms anything
  that would supersede/weaken an existing entry) with the Overwrite
  Confirmation Queue feature already queued — worth checking whether that
  queue's confirm/reject spreadsheet mechanism (once built) is the right
  surface for "revise/retire a POV entry" too, rather than building a
  second, separate confirmation UI.
