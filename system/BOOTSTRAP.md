# Bootstrap a new Claude session

Open a new chat in the Relationship & Business Builder Cowork project (or any compatible AI tool with file access to this folder). Paste one of these as your first message. **Pick the lightest variant that matches what you want to do** — full reloads burn tokens unnecessarily.

The variants are ordered from heaviest to lightest. The default has changed: as of 2026-05-15, the bootstrap is **two small files plus a protocol index** — the heavy constitutional docs are loaded lazily, only when a protocol declares them in its read set.

**Staleness flag, added 2026-09-05 during a repo cleanup pass, not yet resolved:** this file wasn't individually re-verified against the live system in that pass. Specific claims below (the "20 RCs" verification answer, "7 protocols P-001 through P-007" — `system/protocols/` actually holds 39+ today, `system/_sessions/index.json` as the session-memory mechanism, `system/settings.json`/`system/profiles/{profile_id}/` as live paths) may be stale relative to how this project's actual memory and protocol surface work now. Treat this file's *shape* (tiered bootstrap variants, lazy-loading discipline) as the durable idea; verify any specific number or path before trusting it.

---

## Default automatic initialization (recommended)

Use for any session that isn't onboarding a brand-new model. ~10 KB of context, no preamble cost.

`start RB` is deprecated old architecture. Modern RB sessions initialize automatically by reading the manifest/status/protocol index or, in the Custom GPT, by calling `getManifest`, `getStatus`, and `listProtocols`.

> This is the Relationship & Business Builder (RB) system. Please read these files before responding:
>
> - `system/CHARTER.md` (mission, principles, structure, methodology, roadmap pointer — read this first)
> - `system/MANIFEST.md` (current state)
> - `system/STATUS.md` (what's live, partial, designed, not built)
> - `system/protocols/index.json` (machine-readable list of every protocol with its read set, script, cache, inputs, and trigger)
> - `system/_sessions/index.json` (recent session memory index)
> - The top 2 entries in `system/_sessions/index.json` — read those .md files in full so you know what the previous session(s) worked on and what was hanging at handoff.
>
> Do NOT load any other files yet. When I name a task, look up the matching protocol in `protocols/index.json`, load only the files it declares in `reads:`, and run the script if `script:` is set. If the protocol has a `cache:` field and the cache file is fresh, prefer the cache over recomputing.
>
> Tell me in 3–5 lines what you can see — state summary, what's available, and what the last session left in flight. Then ask what we're working on.
>
> When this session ends, write a session memory file via `rb.session_end` (MCP) or `POST /sessions` (HTTP) so the next session inherits the context.

---

## Cold start (full constitutional reload)

Use only when validating a new model can operate RB correctly, or when STATUS.md and the protocols are wrong and you need the source documents to recover.

> This is the Relationship & Business Builder (RB) system. Please read these files in order before responding:
>
> - `system/CHARTER.md` (mission, principles, structure, methodology, roadmap pointer)
> - `system/settings.json`
> - the selected user profile (`system/profiles/{profile_id}/profile.md`, or `system/00_TODD_PROFILE.md` during legacy single-user mode)
> - `system/README.md`
> - `system/01_RB_TENETS.md`
> - `system/ARCHITECTURE.md`
> - `system/SCHEMAS.md`
> - `system/heuristics.md`
> - `system/WHAT_PERSISTS.md`
> - `system/STATUS.md`
> - `system/MANIFEST.md`
> - `system/network_map.md`
> - `system/protocols/index.json`
>
> Then summarize the system back to me in 5–10 lines so I can verify you've loaded context correctly: who I am, what RB is, the signal/state taxonomy, the circle-type rule, and the rough current network state. Then ask me what we're working on today.

---

## Hot start — task-specific (one procedure, scoped load)

Use when you know exactly the procedure to run. Each hot-start variant names the exact protocol; the protocol's own `reads:` field names the files to load.

### Regenerate today.md

> Run protocol `P-001` (see `system/protocols/index.json`). Use the read set declared there. If `system/.cache/daily_brief.json` is fresh (baseline mtime unchanged), the script is the source of truth and you can confirm without re-reading the underlying files.

### Ingest a LinkedIn data export

> Run protocol `P-002` against the file I'm about to upload. Use the read set declared in `system/protocols/index.json`. Output: snapshot, updated baseline, delta report.

### Validate the baseline

> Run protocol `P-003`. Single command: `python3 system/scripts/validate_baseline.py`.

### Find structural gaps (missing cards, contact fields, last_touch)

> Run protocol `P-004`. Cached at `system/.cache/gap_detection.json`.

### Loop ledger view

> Run protocol `P-005`. Cached at `system/.cache/loop_parser.json`.

### Network anchor-gap report

> Run protocol `P-006`. Cached at `system/.cache/network_gap.json`.

### DRR ranking

> Run protocol `P-007`. Cached at `system/.cache/drr_score.json`.

### Pre-conversation briefing for a specific meeting

> I have a meeting with [PERSON or COMPANY] at [TIME]. Look up the matching card under `system/cards/`, any IBs in `system/briefs/` that reference them, the loops for that party (filter `system/.cache/loop_parser.json` by party name), and the relevant section of `system/ARCHITECTURE.md` ("Pre-conversation briefing"). Produce the 11-section brief.

### Intro engine — find an intro for X

> I want to reach [TARGET]. Read `system/ARCHITECTURE.md` ("Same-Circle behavior depends on `circle_type`"), `system/heuristics.md`, `system/.cache/drr_score.json`, and all `system/circles/*.md` (for `circle_type`). Propose 1–3 named intro paths with reasons grounded in evidence and a draft message in my voice.

---

## Quiet-mode start

Use when you want minimum commentary. Stats and source citations are still required (Tenet 2), but no editorial framing.

> This is the Relationship & Business Builder (RB) system. Set verbosity to `quiet`. Read `system/settings.json`, the selected user profile, and `system/MANIFEST.md`. Then [task].

---

## Which variant to use

| Situation | Variant | Approx. cost |
|---|---|---:|
| Routine daily session | Default start | ~5 KB |
| Routine + you know the procedure | Hot start (matching variant) | ~10–30 KB |
| New machine / unfamiliar Claude / system docs changed | Cold start | ~50 KB |
| You want minimum chatter | Quiet-mode start | ~10 KB |

**Cost discipline.** The cold-start read set is 12 files (~50 KB). The default-start read set is 3 files (~5 KB). The hot-start read sets are typically 1–4 files plus one cache JSON. If you only pay the cold cost when you actually need it, sessions run on roughly an order of magnitude less context per turn.

**Cross-model portability.** Hot-start variants name a protocol ID. Any sufficient model that can read `protocols/index.json` can execute the protocol — the JSON declares the read set, the script (if any), the cache path, the inputs, and the trigger. Models without filesystem access can call the same scripts via the MCP server (`system/mcp/`) or the HTTP API (`system/api/`), once those are running.

---

## Verification checklist for the new session

A fresh model that has loaded the default-start context correctly should be able to answer:

- *"How many RCs do I have?"* → **20** (17 inner, 2 broader, 1 dormant_valuable). Source: MANIFEST.md.
- *"What's live vs. partial?"* → Reads from STATUS.md.
- *"What protocols can I run?"* → Reads from protocols/index.json — should name 7 (P-001 through P-007).
- *"What's the SCN-Donnie rule?"* → Should say "I need to load `system/heuristics.md` to answer that" — that's the right behavior under lazy loading.

If the new session answers these correctly after the chosen bootstrap, context loaded. If it tries to answer the SCN-Donnie question without loading heuristics, it's hallucinating — restart with a tighter prompt that emphasizes lazy-load discipline.
