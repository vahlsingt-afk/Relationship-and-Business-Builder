# What Persists

The persistence contract for the Relationship & Business Builder (RB) system. This is a tenet, not a description. Persistent memory is non-negotiable; if anything in this document is ever wrong, the document is wrong before the system is.

**Last verified against the live system: 2026-08-24.** This document stays intentionally high-level — orientation for a fresh session, not exhaustive detail. For the full, verified technical picture of every persistent store (what writes to it, how automated that write is, what's still manual), see [`INTELLIGENCE_PIPELINE_MAP_2026-08-24.md`](INTELLIGENCE_PIPELINE_MAP_2026-08-24.md) (gather → assess → mutate → report, by pipeline stage) and [`EVIDENCE_TO_PERSISTENCE_MAP_2026-08-24.md`](EVIDENCE_TO_PERSISTENCE_MAP_2026-08-24.md) (by evidence type — "I learned X, which file should reflect that"). Both are dated docs, not living ones — if this system keeps evolving, they'll drift too; check their own dates before trusting them blindly the way this document itself just had to be corrected.

## The model in one paragraph

The files in this folder ARE the memory. The Claude session you're in right now is *not* the memory. When this session ends, every fact captured in a file survives; every interpretation that wasn't written to a file is lost. So the rule is simple: if it matters, it goes in a file. The "experienced Chief of Staff feel" is the cumulative effect of months of those files being written, read, and refined — by you, by Claude in successive sessions, and by the data ingestions you run.

## What survives a session close

Everything in `/Users/toddvahlsing/Documents/Claude/Projects/Relationship & Business Builder/system/`:

| File or folder | What it preserves |
|---|---|
| `profiles/{profile_id}/profile.md` | Selected user identity — career arc, theses, capabilities, communication style, engagement boundaries. The lens any RB session reads everything through. `00_TODD_PROFILE.md` is the current legacy seed profile. |
| `README.md` | System overview for new readers. |
| `ARCHITECTURE.md` | The rules — signal taxonomy, dormancy tiers, intro engine logic, what the system will and won't do. |
| `SCHEMAS.md` | The shape of every artifact. Single source of truth for data structure. |
| `heuristics.md` | Cluster-level network rules ("anyone in SCN knows Donnie"; already-known pairs). Read at session start; consulted by the intro engine before any proposal. |
| `baseline_index.json` | The canonical contact graph. Every person ever ingested, with current signal class, last_touch, sources, tags, Circle membership, and notes. **The most load-bearing file.** |
| `ecosystem_intelligence.json` | Persistent industry-agnostic ecosystem graph: companies, brands, vendors, products, deployments, source-backed signals, confidence, risk, relationship coverage, and strategic recommendations. Restaurants are the first domain pack, not the core architecture. |
| `domain_packs/` | Vertical-specific vocabulary layered on the ecosystem graph. `restaurants.json` defines restaurant/operator categories such as POS, KDS, AUV, franchise model, drive-thru, rollout risk, and integration fatigue. |
| `cards/` | Relationship Cards. One per recognized relationship. The narrative layer — narrative arc, trust state, leverage, what's lingering, how to engage. Where lived history meets data. |
| `briefs/` | **Two different things share this folder now — corrected 2026-08-24.** Legacy per-person "Interaction Brief" evidence-rollup files (e.g. `2026-03-16-aamir-rajan-linkedin-evidence-rollup.md`) — last written March 2026, dormant, nothing live generates these anymore. And, separately, the actual daily deliverables: `YYYY-MM-DD-daily-brief.md`/`-intelligence-brief.md` (and their `.json` twins), written fresh every morning by the current pipeline. If you're looking for "what's the proof behind a signal-class promotion" today, that's `interaction_ledger.json` and `system/ri_events/*.jsonl`, not this folder. |
| `circles/` | Circle definitions and current memberships. Goal-anchored network groupings. |
| `intro_brokers.md` | High-leverage connectors by domain. Regenerable — see "What's a template waiting to be run" below; can go stale for months without anything flagging it. |
| `network_map.md` | Network-as-network read — clusters, density, bridges. Regenerable, same staleness risk as `intro_brokers.md`. |
| `loop_ledger.md` (L- namespace) and `eolms/loops.json` (EL- namespace) | Open loops. Promises, intros pending, follow-ups due. Two separate stores by design (legacy vs. richer-lifecycle) — kept namespace-distinct, never summed without checking for cross-namespace duplicates. |
| `ecosystem_intelligence.json` | Persistent industry-agnostic ecosystem graph: companies, brands, vendors, products, deployments, source-backed signals, confidence, risk, relationship coverage, watch list. The "macro graph." Restaurants are the first domain pack (see `domain_packs/`), not the core architecture. 1,700+ entities, 2,000+ signals as of 2026-08. |
| `graphs/micro/<slug>/` | **Micro graphs** — deep, scoped operational-topology graphs built from one specific source document (org structure, franchise map), distinct from the broad ecosystem graph above. Currently one exists: McDonald's US ops. |
| `.cache/intelligence.db` | SQLite — every piece of intelligence the daily scan gathers, durable and queryable, well beyond what any single day's brief shows. 8,000+ items as of 2026-08. |
| `blue_sheets/` | Account-specific sales dossiers (Miller Heiman-style), downstream of the canonical registry above, created only for a targeted account on explicit direction — never auto-created. Not yet common in 2026-06 when this doc was last accurate; one real account (Pollo Campero) is fully activated as of 2026-08. |
| `weekly_plan.json` / `weekly_plan_draft.json` / `weekly_scorecard_draft.json` | Weekly outcomes/priorities (adopted plan + pending draft) and the Friday wins/misses/lessons scorecard. Didn't exist when this doc was last accurate; auto-generates on a Monday/Friday cadence now. |
| `today.md` | The daily brief. Regenerated; the most recent generation persists until the next one runs. |
| `_snapshots/` | Audit trail of baseline snapshots taken before every merge. Rollback-safe. |
| `deltas/` | Reports from each ingestion (LinkedIn delta, promotion proposals, takeout load). Historical record of what changed and why. |

## What does NOT survive a session close

- **The current Claude session's working understanding.** When this thread ends, nothing in my head transfers. The next Claude session reads the files and rebuilds context from them.
- **In-progress reasoning that hasn't been written down.** A draft conclusion, a pattern I noticed, a connection I drew between two relationships — if it didn't land in a file, it's gone.
- **Suggestions made in chat but not captured.** If we discussed a possible Circle, a tag idea, or an intro that didn't get committed to a file, it doesn't survive.

The rule: **if it matters, write it.** This applies to me (the assistant) and to you. When you tell me something important about a relationship — origin story, lingering issue, working channel, sensitivity — it should land in the relevant card or note before this session ends.

## What's a template waiting to be run (not live until generated)

A few files exist as scaffolding. They contain placeholder content until they're regenerated from real data. After regeneration, they hold real content until the next regeneration overwrites them.

- **`today.md`** — Regenerated each day or on demand. Reflects current dormancy crossings, loops past target, queued RC promotions, today's meetings (when calendar is connected), Circle moves, on-deck intro brokers. Asking "regenerate today.md" or "what's important today" produces a fresh version.
- **`intro_brokers.md`** and **`network_map.md`** — Regenerated periodically, by a Claude session doing the analysis directly (no automated script produces either). Computed from `baseline_index.json`. Hand-written notes in `intro_brokers.md`'s `## Notes` section are preserved across regenerations. **Nothing currently checks whether these have gone stale** — as of 2026-08-24 both had sat untouched for roughly three months while the underlying contact graph kept growing. A weekly freshness check is being added to the Friday routine to catch this going forward (see `system/FRIDAY_EOW_ROUTINE.md` once built).

If either of these reads as a template (placeholder text, "(none)" everywhere), it hasn't been regenerated yet. Ask for it.

## What requires a Claude session to actually execute

The system is files plus a Claude session that knows how to operate on them. Without an active session, the files sit there. Things that require a session to run:

- Generating `today.md` from current baseline state.
- Detecting dormancy crossings (computing days-since-last_touch against tier thresholds).
- Producing intro recommendations (matching a target person/company against baseline + Circle membership + intro brokers).
- Ingesting new data (LinkedIn export, Google Takeout, new evidence files).
- Promoting signal classes from new evidence.
- Writing or updating cards.
- Resolving loops or marking them closed.

The files preserve *state*; a session is needed to *act on* that state. There's no daemon running between sessions.

## How to pick up in a new Claude session

When you open a fresh thread (in Claude Cowork or Claude Code) in this workspace folder:

1. **First message to that Claude**: *"This is the Relationship & Business Builder (RB) system. Read `system/settings.json`, the selected user profile, `system/README.md`, `system/01_RB_TENETS.md`, `system/ARCHITECTURE.md`, `system/heuristics.md`, and `system/SCHEMAS.md` first, then summarize the system back to me before we proceed."*
2. **Verify the new Claude has the right context** — it should describe the signal taxonomy, the canonical files, the engagement boundaries, and your career arc. If it's missing pieces, it didn't read enough; have it read more.
3. **Say what you want to do.** Examples:
   - *"Regenerate today.md."* — pulls dormancy crossings, loops, etc., and writes the daily brief.
   - *"I had a meeting with Bruce Sellnow yesterday — here's what we discussed."* — records the interaction and updates last_touch.
   - *"I want to introduce Noelle to Cristina. Draft the message in my voice."* — uses the intro engine rule plus your style guide.
   - *"I just downloaded a fresh LinkedIn archive — process the delta."*
4. **Trust the files over the assistant.** If a Claude session ever contradicts what's in the files, the files win. The assistant is meant to read, write, and reason over them — not override them.

## The Cold-Start Recovery Pack (if anything goes wrong)

If the workspace folder is ever corrupted or you need to reconstitute the system from scratch:

- Most-load-bearing single file: `baseline_index.json`. Lose this and you lose the contact graph and all signal-class state. Snapshots in `_snapshots/` are recovery points.
- Card narrative content: `cards/`. These contain lived-history that's irreplaceable — they should be in your regular backup rotation.
- Interaction/evidence history: `interaction_ledger.json` and `system/ri_events/*.jsonl` (see the `briefs/` correction above — this is where that history actually lives today, not in the `briefs/` folder).
- The ecosystem graph: `ecosystem_intelligence.json`. Company/vendor/deployment intelligence — loss reduces industry-context depth but doesn't break contact-level operation.
- Circles: `circles/`. Definitions plus current memberships.
- The design docs (`ARCHITECTURE.md`, `SCHEMAS.md`, selected profile docs, `WHAT_PERSISTS.md`, `README.md`) are reconstructible from a Claude session if needed but losing them creates rebuild work.

## What this protects against

The failure mode in earlier RB attempts was: months of accumulated relationship intelligence existed only inside a chat assistant's session memory, and when the session degraded or rolled forward, the intelligence was unrecoverable. The KBs and raw exports were fine because those were writeable artifacts. The *intelligence layer* — interpretations, trust signals, momentum reads, connector identification, longitudinal observations — was the lost piece.

This system inverts that. The intelligence layer is the cards, the interaction/RI event history, the ecosystem graph, the tags, the Circle definitions, the loops, the deltas, the today.md briefs, and the breadcrumbs in `notes` fields. Every piece is in a file. The assistant reads, writes, and reasons over those files; it doesn't accumulate intelligence inside itself.

That's the persistent memory. It's already running. As long as the files survive, the experienced-CoS feel survives.
