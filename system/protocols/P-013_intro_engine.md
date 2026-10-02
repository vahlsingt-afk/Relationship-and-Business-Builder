---
id: P-013
title: Intro engine — broker paths to a target
script: system/scripts/intro_engine.py
cache: system/.cache/intro_engine.<sanitized-target>.json
reads:
  - system/baseline_index.json
  - system/active_threads.yaml
  - system/circles/
  - system/heuristics.md
writes: []
inputs:
  - name: target
    description: Person id, person name, or company name.
    required: true
  - name: limit
    description: Max broker paths to return (default 3).
    required: false
  - name: include_suppressed
    description: Include heuristic-blocked candidates (for debugging).
    required: false
trigger: operator-initiated when there's a named target to reach
---

# P-013 — Intro engine

## Purpose

Resolve a target (person, company, or free-text) into a ranked list of broker paths from your existing network. Each path comes with one-line evidence — what proximity signal makes this broker plausible, what active thread it ties to, what heuristic rules were checked.

This is the highest-value missing chief-of-staff verb. All inputs are now in place: DRR with active-thread boost, network gap scoring for cluster anchors, Circles + Circle types, heuristics for cluster suppression, active threads for current-context weighting. The engine composes them.

## How it works

1. **Resolve target.** Try person id → person name → company name → loose company token. If unresolved, treat as free-text and broker-score the full graph based on active-thread signal only.

2. **Find insiders.** Baseline contacts whose `current_company` matches the target's company. For company-typed targets these ARE the primary broker list. For person-typed targets they're shown above the cross-broker list (often co-workers know the target).

3. **Score every candidate broker.** For each non-target, non-VC baseline contact:
   - DRR score (with active-thread boost already applied)
   - Proximity to target: `same_company` (+10), `shared_circles` (×5 per Circle), `matches_thread_company` (+8), `matches_thread_people` (+5)
   - `has_proximity` flag — at least one signal above

4. **Apply heuristics.** Suppress candidates per `system/heuristics.md`:
   - SCN-orbit → Donnie suppression
   - Cluster-internal suppression (anyone in the same `community_chapter` Circle as the target already knows them)
   - Operator-confirmed already-known pairs

5. **Rank.** Sort by (not suppressed, has_proximity, composite_score). That way an LKI insider at the target's company outranks an inner-tier RC with zero relevance to the target.

6. **Reason lines.** Each broker gets a one-sentence narrative: their tier, DRR, and the specific proximity signals that justified the rank.

7. **Notes.** Surface diagnostic context — "no proximity signal found", "target is already inner-tier RC; you may not need a broker", "no insiders at this company → network gap territory."

## Operations

```bash
# Person target by name:
python3 system/scripts/intro_engine.py "Bob Gibson"

# Company target:
python3 system/scripts/intro_engine.py "Toast"

# By id (fastest, unambiguous):
python3 system/scripts/intro_engine.py "bob-gibson" --limit 5

# JSON for downstream consumers:
python3 system/scripts/intro_engine.py "Toast" --json --cache

# Include heuristic-suppressed candidates (debugging):
python3 system/scripts/intro_engine.py "Donnie Boivin" --include-suppressed
```

Or via MCP (`rb.find_intro`) / HTTP (`GET /intro?target=Toast`).

## Output shape

```json
{
  "target": "Toast",
  "target_resolved": { "type": "company", "name": "Toast", ... },
  "insiders": [{"id": "bob-gibson", "name": "Bob Gibson", "drr_score": 73.6, ...}, ...],
  "candidate_brokers": [
    {
      "id": "bob-gibson", "name": "Bob Gibson",
      "drr_score": 73.6, "composite_score": 91.6,
      "proximity": {"same_company": true, "shared_circles": [], ...},
      "has_proximity": true,
      "heuristic_flags": [],
      "suppressed": false,
      "reason": "LKI (DRR 73.6); works at Toast; their company is named in an active thread."
    }
  ],
  "suppressed": [...],
  "suppressed_count": 6,
  "notes": [...]
}
```

## Validation against real targets (2026-05-16)

| Query | Top broker | Why |
|---|---|---|
| `"Toast"` (company) | Bob Gibson (LKI, DRR 73.6, composite 91.6) | Insider at Toast; matches `T-2026-05-bob-gibson-toast-watch`. Exactly the promotion candidate the system already knew about. |
| `"Bob Gibson"` (person) | Jon Franklin, Jeff Pinc, ... (Toast LMI insiders) | Bob's co-workers. None are RC, so the engine surfaces this as a network-gap target. |
| `"Global Payments Inc."` | David Lee + David Pettit (both LKI insiders) | Mike Schwartz is also an insider but lower-DRR; engine ranks above raw familiarity. |
| `"Donnie Boivin"` | (inner-tier note + 6 suppressions) | Engine correctly notes Donnie is already inner-tier and suppresses every SCN/Hospitality-Table contact via the heuristic. |
| `"Patrick Nelson"` | Bruce/Chason/Daran (no proximity) | Engine explicitly flags "no proximity signal — these are warm-introducer candidates, not subject-matter intros." Honest, not fake-confident. |

## Heuristics in code

`system/heuristics.md` is the narrative. The engine encodes the most important rules in code:

- `_HEURISTIC_SUPPRESSED_TARGETS` — target-specific suppression maps (e.g. Donnie → SCN circles).
- `_HEURISTIC_INTERNAL_CLUSTERS` — Circles whose members already know each other.
- `_HEURISTIC_KNOWN_PAIRS` — operator-confirmed pairs.

When `heuristics.md` gains a new rule, mirror it here.

## Failure modes

- **Composite tied at the top.** Multiple candidates with the same composite score show in a tied order. Operator should treat top-3 as a candidate set, not strict ranking.
- **DRR cap pull.** All inner-tier RCs hit the 100 cap, so for low-proximity targets the rankings flatten. The `has_proximity` tier-break mitigates this; the eventual fix is a non-saturating composite or a recency/cluster-relevance multiplier.
- **Stale `heuristics.md`.** The encoded rules will drift from the markdown if not maintained. Quarterly review per the file's maintenance section.
- **Unknown target.** Free-text targets that don't resolve still return broker candidates, but they're ranked purely by DRR + thread context. Surface the note in the output so the operator knows the resolution failed.

## Roadmap

- **Draft intro message in Todd's voice.** V0.1 — given a chosen broker, generate a short message the operator can paste.
- **Multi-hop brokers.** A → B → target paths where B isn't in baseline. Requires a small inference layer on top of existing data.
- **Reciprocity tracking.** Suppress brokers who've been asked for an intro in the last N days (Tenet 11b carry-forward).
- **Heuristic loader.** Parse `heuristics.md`'s pair table at runtime instead of mirroring it in code.

## Voice

Engine output is structural. The reason line is short and evidence-based. The session-level interpretation — *"go with Bob, here's the message"* — is the model's job, not the engine's.
