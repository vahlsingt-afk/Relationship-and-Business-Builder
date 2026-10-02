---
id: P-019
title: Apple Messages + Call History — direct interaction overlay
script: system/scripts/fetch_apple_messages.py + system/scripts/fetch_apple_calls.py + system/scripts/interaction_overlay.py
cache: system/.cache/interaction_overlay.json
reads:
  - ~/Library/Messages/chat.db
  - ~/Library/Application Support/CallHistoryDB/CallHistory.storedata
  - system/inbox/messages.json
  - system/inbox/calls.json
  - system/baseline_index.json
writes:
  - system/inbox/messages.json
  - system/inbox/calls.json
  - (optionally) system/baseline_index.json (when --apply-last-touch is passed)
inputs:
  - name: days
    description: Window to read from chat.db / CallHistory.
    required: false
  - name: include_snippets
    description: Capture 80-char message snippets (default false — metadata only).
    required: false
trigger: scheduled (suggest daily); on-demand before the daily brief; after any session where you texted or called known contacts
---

# P-019 — Apple Messages + Call History overlay

## Purpose

End the hand-maintained `last_touch` era. RB now reads the operator's actual phone and text history from macOS, matches handles against baseline by normalized phone or email, and proposes `last_touch` updates from real interactions.

This is the single highest-leverage data input the system can have. Bruce Sellnow's "daily-cadence text" pattern stops being narrative in `00_TODD_PROFILE.md` — the system sees it directly. Quiet zones validate themselves. Cooling surfaces without you remembering to log a touch.

## What it reads

**macOS only.** Two SQLite databases the OS maintains on the operator's Mac:

- `~/Library/Messages/chat.db` — iMessage AND SMS (the latter via Continuity from your iPhone, if SMS Forwarding is enabled). Contains every message visible in the Messages app.
- `~/Library/Application Support/CallHistoryDB/CallHistory.storedata` — phone calls (via iPhone Continuity) and FaceTime calls. Direction, duration, timestamp, handle.

Both are read-only SQLite files. The fetchers open them with `mode=ro`; nothing is ever written back to the OS-managed databases.

**Direct iOS access is not supported.** Apple locks call logs and SMS down on iPhone — no third-party app can read them via iOS APIs. The Mac route is the only legitimate local-data path. iCloud backup parsing is a different, manual approach not implemented here.

## Privacy posture

The most sensitive data RB touches. Hard rules:

1. **Default metadata-only.** `snippet` is `null` by default. Message body content stays in the OS SQLite store and never enters `system/inbox/`. Pass `--include-snippets` to opt in to truncated (80 char) capture.
2. **Git-ignored.** `system/inbox/messages.json` and `system/inbox/calls.json` are in `.gitignore`. Never commit them.
3. **Never leaves the system.** Don't pipe through any third-party API. Matching is local; the overlay output is local; the daily brief renders locally.
4. **Full Disk Access required.** The fetcher will fail without it, with a clear instruction. This is by design — Apple's privacy gate is the right gate.
5. **In production multi-tenant SaaS:** encrypt at rest, scope strictly to the owning operator, treat as PII subject to deletion-on-request.

## Setup (first time, ~5 minutes)

1. **Grant Full Disk Access** to the process that will run the fetcher:
   - System Settings → Privacy & Security → Full Disk Access
   - Add Terminal (or your IDE — VS Code, etc.) to the list
   - Make sure it's enabled (toggle on)

2. **Run the fetchers** from this repo's root:

   ```bash
   python3 system/scripts/fetch_apple_messages.py --days 365
   python3 system/scripts/fetch_apple_calls.py --days 365
   ```

   First run captures a year of history. Subsequent runs can use a shorter window (e.g. `--days 30`) for faster updates.

3. **Run the overlay** to see who matched:

   ```bash
   python3 system/scripts/interaction_overlay.py
   ```

   Expect: a per-contact summary, a list of proposed `last_touch` updates, and a list of unmatched recurring handles.

4. **Enrich baseline.** Most matches will fail at first because baseline `phone` fields are sparse. The unmatched-recurring-handles list IS the recommendation surface. For each recurring handle you recognize:

   ```bash
   python3 system/scripts/mutations.py touch --id <baseline-id> ...
   # or, if the contact isn't in baseline yet:
   python3 system/scripts/mutations.py contact-add --id <new-id> --phone "+1..." --signal-class LKI
   ```

5. **Apply the proposed updates** when you're confident:

   ```bash
   python3 system/scripts/interaction_overlay.py --apply-last-touch
   ```

   This calls `mutations.touch` for each proposed update, snapshotting + validating per the standard write-back contract.

## How matching works

For each interaction event's `handle`:

1. If the handle contains `@`, treat as email — look up `email` index against baseline.
2. Otherwise, normalize phone: strip non-digits; if 10 digits, prefix `+1`; if 11 digits starting with `1`, prefix `+`; else prefix `+`. Match against `phone` index.
3. No match → record in `unmatched_recurring_handles` if the handle has 3+ events.

The `unmatched_recurring_handles` list ranks by event count. High-count unmatched handles are either important contacts not yet in baseline (promotion candidates) or noise (verification codes, delivery notifications). Operator triages.

## Daily brief integration

The brief now carries a "Direct interaction signal" section with:

- Matched contact summary (messages in/out, calls in/out/missed, last interaction date)
- Proposed `last_touch` updates ranked by staleness
- Unmatched recurring handles ranked by event count

This is sourced from the cached overlay, so no live SQLite read happens during brief generation. The daily-brief cache regenerates when `messages.json` / `calls.json` change.

## Composite signal value

Per-contact `interaction_score` (rough, V0):
```
interaction_score = messages * 1.0
                  + calls_in * 3.0
                  + calls_out * 3.0
                  + calls_missed * 0.5
```

Calls weight more than texts. Missed calls count as faint signal. This isn't exposed in the current overlay output but is the obvious next aggregation step.

## Failure modes

- **chat.db not found.** You're not on macOS, or Messages.app isn't set up. There's no fallback; the fetcher exits non-zero with a clear message.
- **Full Disk Access denied.** SQLite returns "unable to open" or "permission denied." The fetcher catches and prints the FDA grant instructions.
- **All handles unmatched.** Baseline doesn't have phone fields. Run the unmatched-handle triage flow (above) to enrich.
- **Family/personal contacts in baseline.** This system was designed for professional contacts. If you keep your spouse in baseline, their interactions surface like anyone else's — which is fine, but the brief will be weighted toward whoever you text most, not whoever's most professionally important. Use tier carefully.
- **iMessage group chats.** The fetcher pulls per-message-per-handle; group chats produce one event per participant per message. That's accurate but can over-count. V0.1 enhancement: dedup group-chat messages.

## Roadmap

- **Auto-apply last_touch updates** for inner-tier RCs whose interaction is unambiguous (single matched handle, no group chat). Reduces operator friction.
- **Auto-promotion of unmatched recurring handles** to LKI candidates once the operator marks one as recognized — even without filling in a name.
- **Sentiment / cadence analysis** of the message stream — *"this thread is getting one-sided; you're sending 3 messages for every 1 received."* Requires snippet capture.
- **Cross-source unification.** Email overlay + interaction overlay + calendar overlay all produce per-contact recency signal. A unified `last_interaction_at` field computed from all sources, supersedes hand-maintained `last_touch`.

## V0 validation (synthetic data)

End-to-end test in the sandbox: synthetic chat.db with 26 messages across 5 handles, synthetic CallHistory.storedata with 8 calls. Two synthetic numbers (`+15558884444` for Patrick, `+15552223333` for Dave) inserted into baseline temporarily. The fetcher correctly:

- Parsed all 26 messages + 8 calls from SQLite.
- Matched 2 contacts (Dave + Patrick) by phone normalization.
- Did NOT match Bruce's events because Bruce's real baseline phone (+1 720) didn't match the synthetic +1 555 number — correct behavior.
- Proposed last_touch updates with sensible gap-day evidence.
- Surfaced 5 unmatched recurring handles including the verizon noise and the synthetic Bruce number.

Synthetic baseline edits were reverted before commit.
