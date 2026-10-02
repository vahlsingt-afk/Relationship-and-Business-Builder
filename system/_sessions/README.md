# `system/_sessions/` — conversation memory

A short structured summary of each material session with RB. The point isn't to remember every keystroke — it's to remember enough that the *next* session doesn't re-explain the universe.

## Why this exists

Without conversation memory, every new Claude (or ChatGPT, or future model) session starts naive. It loads MANIFEST + STATUS + protocols, knows the current state of the file system, and has no idea what was *just decided*. The operator ends up repeating context that was already explained five minutes earlier in a previous chat window.

With conversation memory, a new session opens with the recent past on the page in a compressed form: what we worked on, what was decided, what's hanging at the end. The cost is ~5-15 KB per session loaded; the benefit is the entire "re-onboarding" tax disappears.

## File shape

One file per session, named `YYYY-MM-DD-HHMM.md`. YAML frontmatter for indexing, markdown body for narrative.

```markdown
---
session_id: 2026-05-16-1900
date: 2026-05-16
end_time: 2026-05-16T19:00:00-05:00
duration_estimate: "~3 hours"
focus_areas:
  - social inbound layer
  - calendar/email overlay
threads_touched:
  - T-2026-05-genius-global-payments
contacts_touched:
  - mike-schwartz
  - donnie-boivin
mutations:
  contact_add: 1
  loop_close: 1
  social_add: 1
status: complete
next_session_should: |
  Roll into intro_engine.py (chief-of-staff roadmap item #5).
---

# Session — 2026-05-16

## What we worked on
- Built the social inbound overlay (P-011)
- Re-shaped accounts.yaml around vahlsingt@gmail.com as primary
- Added multi-account inbox layer

## What was decided
- Social ingestion stays source-agnostic; same pattern as calendar/email
- LinkedIn feed reading happens in the operator's logged-in browser (not scraping)
- Personal calendar feed pending until a second Calendar MCP is wired

## In flight at session end
- Intro engine (#5) not yet built
- Need to wire personal calendar MCP

## Notable findings
- Global Payments interview scheduled Monday 5/18 surfaced only via email overlay — was invisible to file system
- Donnie post overlay matched correctly; thread title matching is a V0.1 backlog item
```

## Fields

| Field | Purpose |
|---|---|
| `session_id` | Filename stem. `YYYY-MM-DD-HHMM`. |
| `date` | ISO date. Used by the index for time-window queries. |
| `end_time` | ISO datetime if available; helps order intra-day sessions. |
| `duration_estimate` | Approximate, narrative. |
| `focus_areas` | Short tags. Used by future search to find sessions by topic. |
| `threads_touched` | Active-thread ids that were created, closed, or updated. |
| `contacts_touched` | Baseline ids that were added, promoted, or otherwise touched. |
| `mutations` | Counts of write operations by kind. Audit signal. |
| `status` | `complete` / `partial` / `aborted`. |
| `next_session_should` | One-paragraph handoff note for the next operator session. |

The body is free-form markdown but conventionally has these sections in this order: *What we worked on*, *What was decided*, *In flight at session end*, *Notable findings*. Some sessions add *Open questions* or *Followups*.

## Discipline

- **Keep summaries tight.** Target 2-5 KB per file. The point is recall, not a transcript.
- **Write at the end of every material session.** Trivial Q&A sessions (one or two messages, no mutations) don't need an entry.
- **Update the index after writing.** `python3 system/scripts/session_index.py` regenerates `system/_sessions/index.json`.
- **Don't edit closed sessions.** If something was wrong, write a correction note in the next session. The record is the record.

## Cold-start integration

Per `BOOTSTRAP.md`, the default cold start reads the most recent 2-3 session summaries alongside MANIFEST + STATUS + protocols/index.json. The new model sees the recent past compressed, picks up where the last operator session left off.

## Privacy

Session memory contains operator narrative — names of contacts, decisions, plans. In V0 it's versioned in git; in production multi-tenant deployments it lives in per-tenant storage and is treated as the most sensitive content in the system.
