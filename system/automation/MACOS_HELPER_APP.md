# macOS Helper App — V1 productization design

The V0 automation (`launchagent_install.py` + plist + manual FDA grant) is the right answer for a developer running their own RB instance. It is **not** the right answer for a non-developer downloading the product expecting "click a button, your phone signal flows in."

This document describes how the productized version works. Not implemented; this is the engineering contract for V1.

**Status: DESIGN ONLY** as of 2026-05-18.

---

## The user experience target

A new RB customer on macOS:

1. Opens the RB web app or desktop client. Sees a card: *"Connect your Mac's phone + text history. The system will automatically refresh interaction signal for you."*
2. Clicks **Connect**.
3. Sees a system-native macOS permission dialog: *"RB Helper would like to read your Messages and call history. This data stays on your Mac unless you opt to sync it."*
4. Approves. The Helper App is installed in `/Applications/`, registered as a login item, and starts a background refresh.
5. Two minutes later, the dashboard shows: *"Connected. 14,847 messages and 412 calls processed. 47 contacts matched, 18 proposed updates."*

No terminal. No `python3`. No Full Disk Access dialog navigation. No plist editing.

## Why a Helper App, not just a LaunchAgent

LaunchAgent works for a developer who can grant FDA to a python binary via Settings. For a normal user, every step in that flow is a fail-point:

- Users don't know what FDA is.
- Users don't know how to find `/usr/bin/python3` in the file picker.
- Users don't have a Python interpreter the system trusts (or any consistent one).
- Users won't paste log paths to debug.

A signed macOS app is the only V1 path that ships to non-developers. Apple has built the entire macOS permission model around app bundles, not loose scripts.

## Architecture

### Helper App bundle

- **Bundle ID:** `com.relationshipbuilder.helper`
- **Distribution:** signed + notarized macOS `.app`, downloaded from RB's web onboarding or shipped via the RB main app's auto-update mechanism. NOT through the Mac App Store (sandboxed apps can't read Messages/CallHistory).
- **Code signing:** Developer ID Application certificate. Notarized via Apple's notary service. Hardened runtime with the right entitlements (none required for SQLite read; FDA gates it instead).
- **Size:** small — Python embedded if needed, or written in Swift for native FDA prompt integration.

### Background behavior

- Registered as a **login item** so it starts when the user logs in.
- Lives as a **menu bar app** (small status icon) — so the user can see it's running and click for status / pause / preferences.
- A background `dispatch_source_timer` triggers refreshes every 30 minutes (configurable).
- On each refresh: read chat.db + CallHistory (read-only), normalize, write to the local RB data directory.

### FDA flow (the native version)

When the user first launches the Helper:

1. Helper checks if it has FDA via `SecCopyPrivilegedReferenceForStorageURL` or by attempting a read.
2. If not: present a native explanatory sheet (using `NSAlert` or a custom `NSWindow`) that says exactly what FDA is and why this app needs it.
3. The sheet has a **Grant Access** button that calls `NSWorkspace.shared.open` on the FDA settings pane (`x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles`).
4. macOS prompts the user to enable the toggle. After they do, the Helper detects the new access state (poll or via `kCFCoreFoundationVersionNumber` notifications) and continues.
5. If the user denies, the Helper offers two paths: *Skip this integration* (RB still works without phone signal) or *Show me again later*.

This flow is the right way to handle FDA on macOS. Other apps that need FDA (1Password, Bartender, iStat Menus) follow the same pattern.

### Data flow — two modes

**Local mode (V1 default).** Everything stays on the Mac. The Helper writes to `~/Library/Application Support/RelationshipBuilder/inbox/messages.json` and `calls.json`. The main RB app (or RB session in Cowork) reads from there. No network transit.

**Sync mode (V1.1).** Opt-in. The Helper additionally posts an encrypted snapshot to RB's cloud, so the daily brief can be generated even when the Mac is off, and so team-mode features can include phone/text signal. The encryption key is held by the user — the cloud sees ciphertext only. Same pattern as 1Password's encrypted vault.

### Telemetry + status

A small status display in the menu bar:

- Green dot: refresh succeeded within last hour.
- Yellow: refresh older than 2 hours.
- Red: FDA denied or last refresh failed.
- Click → opens a window showing: last refresh time, # events captured, # contacts matched, # unmatched recurring handles, link to the RB main app, settings.

## Productization considerations

### Pricing tier alignment

The macOS Helper is a **Pro tier** feature. It requires:

- Apple Developer Program membership ($99/yr to sign + notarize).
- An installable update flow (Sparkle is the standard).
- Customer support for FDA edge cases.

Free tier RB stays web-only with manual ingestion. Pro tier gets the Helper, the Custom GPT, and the team-mode integrations.

### Windows + Linux parity

- **Windows:** Equivalent fetcher for Phone Link (the Microsoft-Apple-Android bridge), iCloud for Windows SMS, or — more realistically — direct iPhone backup parsing via iTunes/Apple Devices. Significantly more friction than macOS. V2 territory.
- **Linux:** No native phone integration. V2+ if at all.

### Apple's evolving stance

Apple has been progressively tightening access to user data on macOS. The `chat.db` read pattern works as of macOS 15 (Sequoia). It may not work the same way in macOS 16 or 17. Engineering needs to:

- Track macOS preview releases.
- Maintain a fallback to iCloud-backup parsing if direct chat.db read becomes impossible.
- Be prepared for Apple to require a private entitlement (which we'd have to apply for).

The current strategy is: build on the chat.db path, monitor, plan for a backup-parsing alternative.

## Implementation sequencing

| Phase | Deliverable | Status |
|---|---|---|
| V0 | LaunchAgent + manual FDA grant | LIVE (operator path) |
| V1.0 | Signed Helper App, native FDA flow, local mode only | DESIGN |
| V1.1 | Encrypted cloud sync, multi-device support | DESIGN |
| V1.2 | Status UI improvements, preferences panel, auto-update via Sparkle | DESIGN |
| V2.0 | Windows fetcher path | DESIGN |
| V2.0 | iCloud-backup fallback for users without Continuity | DESIGN |

The V0→V1.0 jump is the largest. Engineering estimate: ~6-8 weeks for one developer including signing infrastructure, notarization automation, and the FDA UX polish.

## Why this matters for the product

Phone/text signal is the highest-fidelity relationship data the user has. Without it, the dormancy engine relies on hand-maintained `last_touch` — which is the exact problem RB was supposed to solve. With it, the system becomes self-updating from real interactions, which is what makes the chief-of-staff value proposition real instead of aspirational.

The V0 LaunchAgent gets *you* there. The V1 Helper App gets *customers* there. Both are necessary. The V0 one is built today.

---

*Design captured 2026-05-18 alongside the V0 LaunchAgent build. Revisit at V1 kickoff (M0 of the V1 launch plan).*
