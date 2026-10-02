# RB-DEFECT-032: SMS Treated as Communication Metadata, Not Intelligence Source

**Filed:** 2026-06-08
**Severity:** High
**Category:** Intelligence Ingestion / Signal Detection / Daily Brief Quality
**Status:** Implemented and verified (2026-06-08)

## Implementation Result (2026-06-08)

All three layers implemented directly (per "Claude is architecture and engineering"
directive):

- **Layer 1** (`fetch_apple_messages.py`): added `TRUSTED_SENDERS_PATH` /
  `sms_trusted_senders.json` allowlist, `_normalize_handle()`, `load_trusted_senders()`,
  `full_text` column added to the SQL read, `--trusted-fulltext` CLI flag, and
  per-event `full_text` + `full_text_capture_reason: "trusted_intelligence_source"`
  capture gated on (a) the flag being passed, (b) inbound direction, (c) sender on
  the explicit allowlist. Defaults to fully off (empty allowlist when no file exists)
  — opt-in, not opt-out. Verified: compiles clean; `_normalize_handle` correctly
  normalizes phone/email formats; `load_trusted_senders()` safely returns empty set
  when no allowlist file exists.

- **Layer 2** (`sms_content_mutation.py`, new file): mirrors
  `social_content_mutation.py` exactly per the DEFECT-029 playbook — hash-manifest
  dedup, `MIN_TEXT_LENGTH = 60` noise filter, `_resolve_sender()` matches
  phone/email against baseline contacts to carry real source identity into the
  mutation, then calls `intelligence_mutation_engine.run(text, source_title=f"SMS
  from {sender_name}", ...)`. Verified: compiles clean; `process_new(dry_run=True)`
  runs end-to-end and returns a well-formed empty result (no `messages.json` present
  yet in this environment).

- **Layer 3a** (`opportunity_intake.py`): added `Thesis` and `Contradictory Signals`
  fields to the `createOpportunity` markdown writer — `contradictory_signals` is a
  list of `{source, source_trust, confidence, detected_at, signal, interpretation,
  alternative_explanations, recommended_validation}` dicts, rendered as structured
  bullet entries. This is the structured home the defect's mock output needed.

- **Layer 3b** (`intelligence_mutation_engine.py`): added a new
  `contradictory_opportunity_signal` mutation type ("Mutation 6"). Detection: a cue
  list (`"couldn't find"`, `"no evidence of"`, `"doesn't appear"`, etc.) combined
  with a company match against `active_threads.yaml`'s watchlist company set
  (`thread_cos` — hoisted out of the vendor-detection loop so it's always defined,
  fixing a latent `NameError` for messages with no detected vendor entities, which
  trusted-sender SMS commonly is). `source_trust` is derived from `author_match`'s
  `rc_tier` (high for core/inner_circle, medium for other known contacts, unknown
  otherwise); confidence fixed at 0.65 (known author) / 0.45 (unknown) — always
  below auto-apply threshold, so contradictions always require human confirmation
  (`requires_confirmation: true`). Output carries `interpretation`,
  `alternative_explanations`, and `recommended_validation` ready to flow straight
  into the new opportunity schema fields from Layer 3a.

  **Live-tested** with text resembling Jeff Coffland's actual SMS ("I went through
  McDonald's files and couldn't find any market with Foods Connected listed as a
  provider..."): correctly produced two `contradictory_opportunity_signal`
  mutations (one per matched watchlist company — McDonald's and Foods Connected),
  each with `source_trust: medium`, `confidence: 0.65`, `requires_confirmation: true`,
  full interpretation/alternatives/validation payloads, and properly-cased company
  display names (fixed a `"Mcdonald'S"` title-casing artifact via a small
  `_display_company_name()` helper that capitalizes only the leading letter of each
  apostrophe-delimited segment).

  **Regression**: `system/tests/test_external_content_mutation.py` and
  `system/tests/test_relationship_mutation_engine.py` — 49/49 passed.

**Net effect**: Jeff Coffland's signal — previously discarded at the 80-char
snippet truncation before any extraction stage could see it — now has a complete
path: full text capture (allowlist-gated) → mutation engine →
`contradictory_opportunity_signal` → structured `contradictory_signals[]` field on
the opportunity record → (via RB-DEFECT-031's `overnight_change_digest`, which
counts mutation-log entries) surfaced in the next morning's brief as "what changed
overnight," exactly as the defect's expected-output mock specified.

**Remaining manual step**: populate `system/sms_trusted_senders.json` with Jeff
Coffland's (and other trusted sources') normalized handles to activate the
allowlist — this is an explicit user-controlled config file by design, not
something to auto-populate.

## Confirmed root cause — and it's *more structurally severe* than the filed defect implies

The defect frames this as the same "wiring gap" pattern as RB-DEFECT-029
(content seen → never reaches the mutation engine). Investigation confirms that
framing is correct **but incomplete** — for SMS, there's a deeper problem:

> **The full message text never lands anywhere RB can see it.**

`refresh_sources.py:615` `refresh_messages()` → `fetch_apple_messages.py` reads
`~/Library/Messages/chat.db` and writes only an **80-character snippet** plus
metadata to `system/inbox/messages.json` (confirmed at `fetch_apple_messages.py:67,103`,
file is git-ignored). Jeff Coffland's actual message — "I search McDonald's files
and couldn't find any market with Foods Connected as a provider..." — is well over
80 characters. **It would have been truncated before any extraction stage could see
it.** The only thing downstream (`apply_sms_last_touch_updates`,
`refresh_sources.py:639`) does with SMS events is bump a contact's `last_touch`
timestamp. Content is never inspected.

So this is not "wiring exists, just not connected" (DEFECT-029's shape) — it's
**"the raw material is discarded at ingestion."** Two layers are missing, not one:

```
Current:    chat.db → 80-char snippet → last_touch bump → discarded
Required:   chat.db → full text (trusted-sender allowlist) → mutation engine →
            opportunity-linked contradictory-signal mutation → brief surfacing
```

## Two further structural gaps confirmed (beyond the missing text)

1. **Opportunity records have no thesis/assumptions field to attach a contradiction
   to.** `opportunity_intake.py` createOpportunity (lines 236-251) schema is:
   `id, company_id, company_name, title, status, momentum, strategic_fit, summary,
   next_expected_action, domain_tags, referral_source, recruiter_contact`. No
   `thesis`, `assumptions`, or `contradictory_signals` field exists — confirmed via
   grep, zero hits. Even if the SMS *had* reached the mutation engine, there was
   nowhere structured for "this challenges assumption X" to land.
2. **Trust scoring exists only at the contact level, never propagated to message
   content.** `manual_relationship_intake.py:573` computes a per-contact
   `trust_score` (0-100). But nothing carries that score forward to weight a
   *specific message's* extracted signal — "Jeff Coffland (trust: high) said X"
   currently can't become "X enters the system at high-confidence because Jeff is
   high-trust." This is the missing link for "Source Trust: High" in the defect's
   expected-output mock.

## Architecture for the fix — three layers, in dependency order

### Layer 1: Capture full text for trusted senders (the actual blocker)

Extend `fetch_apple_messages.py` so that, for senders matching a **trusted-contact
allowlist** (baseline contacts above a `trust_score` threshold — reuses the existing
`manual_relationship_intake.py:573` score, no new scoring concept needed), the full
message body is captured rather than an 80-char snippet. This is a privacy-bounded
expansion — *not* "capture everyone's full texts," but "capture full text from people
RB already knows are high-trust intelligence sources." Output: a parallel
`system/inbox/messages_full_text.jsonl`-style structure (or an extended field on
existing event records) gated by the allowlist, separate from the lightweight
metadata stream that continues serving `last_touch`.

### Layer 2: Route trusted-sender SMS text through the mutation engine

New thin caller — `sms_content_mutation.py`, structurally identical to
`social_content_mutation.py` (DEFECT-029's pattern: hash-manifest dedup, calls
`intelligence_mutation_engine.run(text, source_title=f"SMS from {sender}",
source_date=..., ...)`./. This is a new *caller* into the existing engine, exactly
the DEFECT-029 playbook — no new extraction logic.

### Layer 3: Two schema additions the engine's output needs to land somewhere

a. **`opportunity_intake.py` schema** gains `thesis` (the working assumption an
opportunity rests on, e.g. "Foods Connected has an established McDonald's
relationship") and `contradictory_signals` (list of `{source, signal, confidence,
interpretation, alternative_explanations, recommended_validation, detected_at}`).
This is the structured home for exactly the mock output the defect specifies
("Contradictory Opportunity Signal... Alternative Explanations... Recommended
Validation").

b. **New mutation type** in `intelligence_mutation_engine.py`, alongside the
`thesis_alignment_detected`/`engagement_opportunity` types DEFECT-029 just landed:

```
contradictory_opportunity_signal(opportunity_id, source_person, signal_text,
                                  source_trust, confidence, alternative_explanations)
  → opportunity mutation: append to contradictory_signals[], no thesis auto-overwrite
  → relationship mutation: tag source_person as "intelligence_source"
  → brief surfacing: "Overnight Knowledge Mutations" + "What Changed Since
    Yesterday" (RB-DEFECT-031's overnight_change_digest — same mutation-log
    plumbing, this type just needs to be in the counted set)
```

`source_trust` is populated by reading the sender's existing `trust_score`
(`manual_relationship_intake.py`) at mutation time — closing the trust-propagation
gap without inventing a new scoring system.

## Scope for Codex

1. Extend `fetch_apple_messages.py` with a trusted-sender full-text capture path,
   gated on existing baseline `trust_score` (Layer 1 — the actual unblock).
2. Build `sms_content_mutation.py` mirroring `social_content_mutation.py`'s
   hash-manifest + `intelligence_mutation_engine.run()` call pattern (Layer 2).
3. Add `thesis` + `contradictory_signals` fields to the opportunity schema in
   `opportunity_intake.py` (Layer 3a).
4. Add `contradictory_opportunity_signal` mutation type to
   `intelligence_mutation_engine.py`, reading source `trust_score` at mutation time,
   landing in the same confidence-scored mutation-log infrastructure DEFECT-029 uses
   — and ensure it's included in DEFECT-031's `overnight_change_digest` counted types
   (Layer 3b).
5. Confirm "Overnight Knowledge Mutations" brief renderer surfaces the new type.

## Non-goals

- Not a rewrite of `fetch_apple_messages.py`'s metadata pipeline — `last_touch`
  tracking via snippets continues unchanged for non-trusted senders. This is an
  *additive*, allowlist-gated full-text path.
- Not a new trust-scoring system — reuse `manual_relationship_intake.py`'s existing
  per-contact `trust_score`.
- Not a parallel extraction pipeline — Layer 2 is a new *caller* into the existing,
  working `intelligence_mutation_engine`, per the DEFECT-029 playbook exactly.
- No retroactive recovery of the Jeff Coffland SMS itself — it can be entered
  manually via `/intelligence/mutate` as a one-off; the architecture fix is about
  every *future* high-trust-sender message.
