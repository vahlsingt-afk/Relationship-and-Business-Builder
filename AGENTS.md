# RB (Relationship & Business Builder) — instructions for Codex

RB is Todd's Chief of Staff for relationship and account intelligence — currently centered on his Global Payments/Genius sales role (Blue Sheets, Master Account Plans, Account Background Briefs, Competitor Intelligence, a daily brief, a loop ledger), with an earlier general-relationship-network layer (baseline contacts, Relationship Cards, Circles) still underneath it. `system/README.md` has the full picture; `system/api/custom_gpt_instructions_compact_8k.md` is the detailed, per-intent routing spec (what to call for "intel", "brief", account questions, uploads, etc.) — read it when you need the precise rule for a specific request, not just this file.

## How to reach RB

You have trusted shell access to this repo, but no Custom GPT Actions access — that's a real OpenAI platform limitation (Actions are scoped to the specific Custom GPT they're configured on, and Codex only ever sees public Apps/plugins/Skills), not something fixable from here. **You reach RB directly instead, over its real live API, via `system/scripts/rb_cli.py`:**

```bash
python3 system/scripts/rb_cli.py list                                    # every real, callable operation
python3 system/scripts/rb_cli.py describe <operationId>                  # full parameter schema for one
python3 system/scripts/rb_cli.py call <operationId> '{"key": "value"}'   # call it for real
```

This hits the same live API, with the same real data, the same real write paths, and the same server-side validation and authorization gates as every other RB surface (the Trusted Chat Client included) — nothing here is a simulation or a lighter/looser version. `call` prints the real HTTP status and the real response body; there is no other signal of success.

## The one rule that matters most

**A mutation happened only if you ran `call` this turn and saw a real 2xx response.** Never say "logged" / "added" / "updated" / "confirmed" from memory, from what a request *should* do, or from a plausible-sounding guess. If you didn't run it and see the response, it didn't happen — RB's own history is full of confirmed incidents (across every surface, not just this one) where a model narrated a confident, well-formatted "done" with zero real API calls behind it. Same standard applies to reading: don't answer an "intel" / "brief" / account-status style question from your own knowledge or from an earlier turn's cached understanding — call the real operation and read the real response.

## Review-first, always

RB's persistence architecture deliberately never lets free text become a canonical claim by itself. Structured facts go through named, narrow operations (`addBlueSheetEvidence`, `addCompetitiveNote`, `confirmProposal`, etc.) with real fields, not paragraphs. Several write operations require explicit human authorization as a real, checked field — e.g. `createBlueSheetAccount` requires `user_authorization_quote`, a verbatim quote of what Todd actually said requesting a new Blue Sheet, not a paraphrase. If a call comes back rejected for a missing/invalid field like that, it means the safety gate is working as intended — don't route around it by inventing a plausible-sounding quote; ask Todd for the real one, or don't call it.

Personal relationships are explicitly out of scope: business and business-adjacent content should be recorded, purely personal relationships/content should not (see `personal_relationship_guard.py`). When genuinely unsure whether something is business-relevant, say so rather than guessing either way.

## Some real operations you'll likely want

Run `list` for the full current set (55 as of 2026-08-28) — this is a starting map, not exhaustive:

| Intent | Operation |
|---|---|
| Today's brief | `getDailyBrief`, `getDailyBriefPart2` — display the returned markdown verbatim, never regenerate/reword it yourself |
| Status of an active account | `listBlueSheetAccounts`, `getAccountStatus` |
| Pre-engagement research on a brand | `listAccountResearch`, `getAccountResearch`, `generateAccountBackgroundBrief` |
| A vendor's own multi-account portfolio (e.g. Worldpay) | `listMasterAccountPlans`, `getMasterAccountPlan` |
| Competitor tracking | `listCompetitors`, `getCompetitorProfile`, `createCompetitor`, `addCompetitiveNote` |
| Broad "what do we know about X" | `queryIntelligenceIndex` |
| A file/document with real content to process | `uploadAndIngestFile` |
| Open loops | `getLoops`, `closeLoop` |

## What not to do

- Don't hand-generate a brief, account status, or research summary from your own knowledge/memory — call the real operation.
- Don't retry a rejected write by inventing the field it was missing (an authorization quote, a real id) — surface the rejection and ask, or find the real value first.
- Don't treat a 4xx/5xx response as a soft failure to quietly work around — it means something real needs fixing (a wrong id, missing auth, a genuine bug) and should be surfaced, not papered over.
