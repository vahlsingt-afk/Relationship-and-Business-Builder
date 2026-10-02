---
id: P-012
title: Conversation memory
script: system/scripts/session_writer.py + system/scripts/session_index.py
cache: (the index.json IS the cache — regenerated from the .md files on demand)
reads:
  - system/_sessions/index.json
  - system/_sessions/*.md
writes:
  - system/_sessions/<session-id>.md
  - system/_sessions/index.json
inputs:
  - name: focus_areas
    description: Short tags describing what this session worked on.
    required: false
  - name: threads_touched
    description: Active-thread ids that were created, closed, or updated.
    required: false
  - name: contacts_touched
    description: Baseline ids added, promoted, or otherwise touched.
    required: false
  - name: mutations
    description: Counts of write operations by kind.
    required: false
  - name: body
    description: Structured narrative — what we worked on / what was decided / in flight / notable findings.
    required: false
trigger: end of every material session (creator decides — trivial Q&A sessions don't need an entry)
---

# P-012 — Conversation memory

## Purpose

Persist the *narrative* of a session — what we worked on, what was decided, what's hanging — so the next session doesn't re-derive it from the file system or re-ask the operator.

## Why this exists

Even with MANIFEST + STATUS + protocols/index.json, a new session loaded cold has no idea what was *just decided*. It can see the current state of the files but not the reasoning that led to them. The operator ends up re-explaining context. Conversation memory closes that gap by capturing 2-5 KB of structured narrative at session end, loaded automatically on the next cold start.

## File shape

One file per session at `system/_sessions/<session-id>.md`. Filename is `YYYY-MM-DD-HHMM`. YAML frontmatter carries indexable fields; markdown body carries narrative.

See `system/_sessions/README.md` for the full schema.

## Discipline

- **Write at end of every material session.** A session that made mutations, touched threads, or surfaced findings warrants a memory file. A two-message clarification doesn't.
- **Keep summaries tight.** Target 2-5 KB. The point is recall, not transcript.
- **Refresh the index after writing.** `mutations.py session-end` and the MCP/HTTP entry points do this automatically. `python3 system/scripts/session_index.py` runs it manually.
- **Don't edit closed sessions.** If something was wrong, write a correction in the next session. The record is the record.

## Operations

```bash
# CLI:
python3 system/scripts/mutations.py session-end \
    --focus-areas "intro engine" "thread closure" \
    --threads-touched T-2026-05-genius-global-payments \
    --contacts-touched mike-schwartz \
    --mutations '{"contact_add": 1, "loop_close": 1}' \
    --status complete \
    --next-session "Pick up DRR eval framework" \
    --body-file /tmp/body.json

# Library:
python3 system/scripts/session_writer.py --in /tmp/summary.json --reindex

# MCP: rb.session_end (writes) + rb.recent_sessions (reads top N)
# HTTP: POST /sessions (writes) + GET /sessions/recent?limit=N (reads)
```

## Cold-start integration

`BOOTSTRAP.md` default cold start now loads `system/_sessions/index.json` (small) plus the top 2 .md files (the most recent and the one before it). That's another ~10 KB on top of the existing default-start budget. A new session opens knowing the recent past.

The hot-start variants don't load session memory — they're scoped to a specific protocol and don't need broad context.

## Failure modes

- **Bloat.** If memory files start exceeding ~10 KB each, the cold-start cost compounds. Discipline: keep `body` sections to bullets, not paragraphs. Each bullet one line.
- **Missing index entries.** The index is regenerated from the .md files; if a file lacks valid frontmatter it's silently skipped. Always include frontmatter.
- **Conflicting facts across sessions.** Newer session's claim wins; if necessary, the new session should explicitly note "supersedes X from session 2026-05-15-0900."

## Privacy

Session memory is narrative — names of contacts, business decisions, plans. In V0 it's versioned in git. In productized multi-tenant deployments it lives in per-tenant encrypted storage and is treated as the most sensitive content in the system.

## Roadmap

- A `rb.session_search` tool that filters by contact, thread, or date range. Out of scope for V0; the index alone is enough for "last N" queries.
- Auto-summarization: a model post-processes a chat transcript into the structured form, rather than the operator hand-writing it. Once exercises a few sessions and the shape is settled.
- Cross-session relationship tracking: e.g. "this contact has been touched in 4 of the last 10 sessions" — a kind of session-side dormancy signal.
