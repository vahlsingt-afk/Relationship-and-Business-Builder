# RB-DEFECT-029: External Content Consumed as Reading Material, Not Intelligence

**Filed:** 2026-06-07
**Severity:** High
**Status:** Implemented and verified — 2026-06-08

## Confirmed root cause

This is **not** a quality failure of the mutation engine — it's a **wiring gap**.
`intelligence_mutation_engine.py` (landed in RB-DEFECT-026, commit `d570565`) is a
fully-built 5-stage pipeline (entity extraction → resolution → mutation generation →
confidence scoring → brief reporting) that converts raw text into knowledge-graph
mutations, executive-POV updates, and brief-surfaced "Overnight Knowledge Mutations."

But it is **only reachable two ways**:
- `POST /api/artifacts` with `pipeline == "intelligence"` ([server.py:~4726](../system/api/server.py))
- `POST /intelligence/mutate` ([server.py:5033-5066](../system/api/server.py#L5033))

Both require a human (or the Custom GPT via `ingestContent`) to **explicitly submit
text**. There is **zero wiring** between this engine and the automated LinkedIn
ingestion path (`linkedin_export_watcher.py`, `morning_pipeline.py` — confirmed via
grep, no references in either file). Confirmed empirically: the Bruce Nelson post
never appears anywhere in the repo as raw text, inbox item, delta, or ledger entry —
it didn't fail mutation, it never arrived at the door the mutation engine watches.

Current flow really is:

```
LinkedIn post → seen by user → (nothing automatic happens) → gone
```

The mutation engine itself is not the broken part. The **automatic trigger** from
"content the user encountered" → "text submitted to the mutation pipeline" does not
exist for social/article content. (Structured feeds like
`market_signals_earnings.jsonl` do have dedicated automated ingestion — this gap is
specific to unstructured social/article content.)

## Architecture for the fix

Two complementary pieces — both needed, neither sufficient alone:

### 1. Automatic capture-to-pipeline wiring for LinkedIn content

`linkedin_export_watcher.py` already detects new export content (per RB-DEFECT-024d
lineage). Extend its processing step so that **post/article-type items** (not
messages, not connection events — specifically long-form authored content encountered
in feed/engagement exports) are passed as `text` + `source_title`/`source_url`/
`source_date` directly into `intelligence_mutation_engine.run(...)`
([intelligence_mutation_engine.py:715](../system/scripts/intelligence_mutation_engine.py#L715)),
the same call shape `server.py` already uses for the `/intelligence/mutate` path. This
reuses the existing engine untouched — it's a new *caller*, not new extraction logic.

### 2. Close the relationship-graph and opportunity-detection gap in the engine's output

The investigation found the engine produces knowledge-graph mutations and brief
entries, but — confirmed in its own design — **does not** touch relationship-graph
affinity/classification or generate opportunity records. That matches exactly what
the defect calls out as missing ("Recognized philosophical alignment between Bruce
Nelson and Todd Vahlsing... Updated affinity score... Identified potential LinkedIn
response opportunity").

Design: when the engine's entity-resolution stage resolves a mutation's source to a
**known person** (not just a company/brand entity — i.e., the author of the content
is in the relationship graph, as Bruce Nelson is, per `baseline_index.json:9396`),
add a second mutation class alongside the existing knowledge-graph mutation:

```
thesis_alignment_detected(person, thesis_id, alignment_strength)
  → relationship_graph mutation: affinity_score delta, thought-partner tag
  → opportunity record: {type: "engagement_opportunity",
                          basis: "thesis_reinforcement",
                          person, source_artifact, suggested_action}
```

Both should land in the same confidence-scored, timestamped mutation-log structure
the engine already uses for knowledge-graph mutations — this is an additional
mutation *type*, not a parallel system.

## Scope for Codex

1. Wire `linkedin_export_watcher.py` (or `morning_pipeline.py`'s post-watcher step) to
   call `intelligence_mutation_engine.run()` automatically for authored long-form
   content items, using the existing `server.py:5033` call pattern as the template.
2. Extend `intelligence_mutation_engine.py`'s mutation-generation stage to detect
   thesis-alignment-by-known-person and emit relationship-graph + opportunity mutation
   records (new mutation type, same log/confidence/brief-surfacing infrastructure).
3. Confirm "Overnight Knowledge Mutations" brief section ([server.py:~5070](../system/api/server.py))
   also surfaces the new relationship/opportunity mutation types, not just
   knowledge-graph ones.

## Non-goals

- No rewrite of the mutation engine's extraction/scoring core — it's sound, just
  unreachable from this content path.
- No retroactive processing of historical LinkedIn content the user has already seen
  (the Bruce Nelson post itself can be backfilled manually via the existing
  `/intelligence/mutate` endpoint as a one-off; the architectural fix is about every
  *future* post).

## Implementation Result

The original design correctly identified the missing trigger but named the wrong
physical source for post bodies. LinkedIn archive exports currently provide
connections and messages, not the normalized long-form feed text required by the
mutation engine. The implemented trigger therefore operates at both real content
surfaces:

1. `linkedin_freshness_bridge.process()` now calls
   `intelligence_mutation_engine.run()` while copied post/screenshot text and explicit
   author metadata are still available.
2. `social_content_mutation.py` scans new posts in `social.feed.json`, processes each
   post once using a durable hash manifest, and is invoked by both full and fallback
   morning-pipeline scan paths.

The mutation engine now accepts source-author identity and, when a known baseline
contact independently reinforces a detected thesis, emits:

- `thesis_alignment_detected`
  - updates the baseline contact's capped `affinity_score`
  - adds the `thought-partner` relationship tag
  - records thesis/source evidence in contact notes and the mutation log
- `engagement_opportunity`
  - records an open opportunity with basis `thesis_reinforcement`
  - preserves person, thesis, source artifact, confidence, and suggested action

Both types use the existing confidence triage and knowledge-mutation log. Unknown
authors and low-confidence alignments are not auto-applied.

The Daily Brief's `Overnight Knowledge Mutations` renderer and
`build_mutation_brief_block()` now surface relationship alignments and engagement
opportunities. Their date filtering was also corrected to use UTC consistently with
UTC mutation timestamps.

Verification:

- focused and broader LinkedIn/intelligence regression suite: 337 passed
- Python compilation: passed
- diff validation: passed
