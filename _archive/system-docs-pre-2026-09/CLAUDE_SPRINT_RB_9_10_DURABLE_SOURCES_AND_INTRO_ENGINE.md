# Claude Sprint — RB 9.10 Durable Sources + Introduction Engine

Date scoped: 2026-05-25
Owner: Todd + Claude/Codex
Theme: make the Daily Brief trustworthy end-to-end, then upgrade the introduction engine from structural ranking to governed Chief-of-Staff action.

## Sprint Goal

RB should enter each morning with fresh, multi-account relationship sources and a working delivery path. Once the trust surface is stable, the introduction engine should become a governed operating layer: specific intro paths, evidence, reciprocity/rate limits, draftable asks, and persistence hooks.

Success means the daily brief no longer hides stale sources, ChatGPT Actions can retrieve the canonical brief, and introduction recommendations are useful enough to act on without burning relationship capital.

## Workstream 1 — Multi-Account Google Sources

Current state:
- `system/inbox/accounts.yaml` models both active identities:
  - `personal` / `vahlsingt@gmail.com` / `[calendar, email]`
  - `bridgepoint` / `todd@bridgepointops.com` / `[calendar, email]`
- `system/scripts/fetch_google.py` now supports:
  - `--account personal`
  - `--account bridgepoint`
  - `--account all`
  - per-account token files under `~/.config/rb/google_token.<account>.json`
- Required Google API libraries are installed.
- Blocker: `~/.config/rb/google_client.json` is missing.

Tasks:
1. Create or obtain a Google OAuth Desktop client with Gmail + Calendar APIs enabled.
2. Place it at `~/.config/rb/google_client.json`.
3. Run first-time auth for each account:
   ```bash
   python3 system/scripts/fetch_google.py both --account personal --mailbox both --days 14
   python3 system/scripts/fetch_google.py both --account bridgepoint --mailbox both --days 14
   ```
4. Confirm files exist and are fresh:
   - `system/inbox/calendar.personal.json`
   - `system/inbox/email.personal.json`
   - `system/inbox/email_sent.personal.json`
   - `system/inbox/calendar.bridgepoint.json`
   - `system/inbox/email.bridgepoint.json`
   - `system/inbox/email_sent.bridgepoint.json`
5. Run:
   ```bash
   python3 system/scripts/refresh_sources.py --email --calendar --save-health --refresh-signals
   python3 system/scripts/source_health_report.py
   ```

Acceptance:
- Source health no longer flags `email:personal`, `calendar:personal`, or `calendar:bridgepoint` as stale/missing.
- `load_calendar()` and `load_email()` aggregate both accounts with `account_id` tags.
- Morning pipeline can run Google fetch as a non-failing step once tokens are present.

## Workstream 2 — Apple Messages/SMS + Phone Logs

Current state:
- Added `system/scripts/apple_access_check.py`.
- Local preflight passed:
  - Messages/SMS readable: 94,760 rows.
  - Call history readable: 685 rows.
- `refresh_sources.py --messages --calls --save-health` succeeded:
  - 3,423 message events captured for 30 days.
  - 103 call events captured for 30 days.
- Morning pipeline now includes a non-fatal Apple access preflight before building the brief.

Tasks:
1. Keep `apple_access_check.py` in the morning pipeline.
2. Ensure source health treats Messages/SMS and calls as expected Apple-user sources, not optional curiosities.
3. Improve recovery copy for Apple users:
   - Full Disk Access for Terminal or the RB app wrapper.
   - iPhone Text Message Forwarding for SMS.
   - Calls on Other Devices / Calls From iPhone for phone logs.
4. Consider adding a setup command that opens the relevant macOS preference panes and the RB app-wrapper folder.

Acceptance:
- A Mac user can run one command and know whether SMS and phone logs are enabled.
- The daily brief clearly says when Messages/SMS or calls are unavailable.
- Interaction overlay refreshes after messages/calls refresh.

## Workstream 3 — API Daemon + Durable Tunnel

Current state:
- Named tunnel exists:
  - URL: `https://rb-api.bridgepointops.org`
  - Tunnel: `rb-api`
- OpenAPI specs and `settings.json` now point to the named tunnel.
- `system/scripts/durable_tunnel_install.py` exists and installs LaunchAgents.
- Cloudflared tunnel can register connections.
- Blocker: local API daemon is still hitting macOS `Operation not permitted` on launchd imports, even with app wrapper and vendored runtime attempts.

Tasks:
1. Decide the daemon strategy:
   - Option A: grant Full Disk Access to the Command Line Tools Python binary and verify.
   - Option B: build a proper app-bundled Python runtime or venv that launchd can read.
   - Option C: use a different host process already trusted by macOS.
2. Make `durable_tunnel_install.py status` accurately distinguish:
   - tunnel process running,
   - API process running,
   - public URL reachable,
   - API auth valid.
3. Run:
   ```bash
   curl -i -H 'x-api-key: localtest' http://127.0.0.1:8765/health
   RB_API_KEY=localtest python3 system/scripts/tunnel_health_check.py --json
   RB_API_KEY=localtest python3 system/scripts/task_delivery_check.py --live --json
   ```
4. Republish the Custom GPT Action schema after URL/schema changes.

Acceptance:
- Local API health returns 200 after reboot/login.
- Public tunnel health returns 200 or expected authenticated response.
- Custom GPT can call `getDailyBrief` and `getBriefHealth` without `ClientResponseError`.
- Daily brief task failure is no longer attributable to a dead tunnel.

## Workstream 4 — Full Morning Pipeline Test

Current state:
- `morning_pipeline.py` now preflights Apple access and attempts Google multi-account fetch before cache rebuild/publish.
- Previous daily brief failure exposed that `task_delivery_check.py` trusted launchd too much. It now checks app wrapper logs and marks stale/missing pipeline cache as fail.

Tasks:
1. After Google OAuth + API daemon are fixed, run:
   ```bash
   python3 system/scripts/morning_pipeline.py --json
   python3 system/scripts/task_delivery_check.py --live --json
   ```
2. Verify artifacts:
   - `system/.cache/morning_pipeline.json`
   - `system/published/daily/latest_brief.json`
   - `system/published/daily/latest.html`
3. Verify the Custom GPT daily brief path reads the same canonical artifact.
4. Confirm backup email copy no longer claims native Task delivery if native Task failed.

Acceptance:
- Morning pipeline passes required steps.
- Daily brief starts with source freshness/trust status.
- If any feed is stale, the brief names the stale feed and recovery command.
- Task delivery check catches hidden app-wrapper or launchd failures.

## Workstream 5 — Stale Source Cleanup

Current source health after Apple refresh:
- Tier 1:
  - `calendar:bridgepoint` stale.
  - `email:personal` stale.
  - `calendar:personal` not_configured in the latest report, likely because it needs durable Google refresh or health mapping cleanup.
- Tier 2/3:
  - `linkedin_messaging` missing.
  - `social_engagement` stale.
  - `social_own_posts` stale.
- Tier 4:
  - `relationship_signals` needs rebuild after primary refresh.
  - `strategic_operators` needs rebuild/cache.

Tasks:
1. Update source-health recovery strings to prefer durable `fetch_google.py --account ...` before session raw captures.
2. Re-run source health after Google OAuth to clear tier-1 warnings.
3. Decide the LinkedIn/social refresh path:
   - LinkedIn export watcher,
   - browser capture,
   - official API where possible.
4. Rebuild derived caches:
   ```bash
   python3 system/scripts/refresh_sources.py --all --save-health --refresh-signals
   python3 system/scripts/strategic_operators.py --cache --json
   ```

Acceptance:
- Tier 1 and Tier 2 sources are fresh or explicitly unavailable with recovery.
- Daily brief trust level is not `under_instrumented` for email/calendar/messages/calls.

## Workstream 6 — Cleanup / Repository Hygiene

Known cleanup items:
- Dirty worktree includes both intended sprint changes and unrelated edits:
  - `system/scripts/fetch_google.py`
  - `system/scripts/morning_pipeline.py`
  - `system/scripts/refresh_sources.py`
  - `system/scripts/task_delivery_check.py`
  - `system/scripts/update_tunnel_url.py`
  - `system/api/openapi*.yaml`
  - `system/settings.json`
  - `system/inbox/accounts.yaml`
  - unrelated-looking edits in `linkedin_own_engagement.py`, `market_signals.py`, `ri_intake.py`, `system/MANIFEST.md`, and defect docs.
- New files:
  - `system/scripts/apple_access_check.py`
  - `system/scripts/durable_tunnel_install.py`
  - `system/scripts/source_health_report.py`
  - `system/vendor/`
- `system/vendor/` should be reviewed carefully before committing. It may be too large/noisy for repo tracking; prefer venv/app-bundle/install script if possible.

Tasks:
1. Split changes into coherent commits or PR chunks:
   - Durable delivery/tunnel.
   - Multi-account source refresh.
   - Apple source preflight.
   - Source health/reporting.
   - Intro engine sprint work.
2. Decide whether `system/vendor/` is committed, ignored, or replaced.
3. Update `system/MANIFEST.md` for new scripts once final.
4. Ensure generated inbox/cache files remain untracked.
5. Run py_compile with local pycache:
   ```bash
   PYTHONPYCACHEPREFIX=/private/tmp/rb-pycache python3 -m py_compile system/scripts/*.py
   ```

Acceptance:
- No accidental generated artifacts are committed.
- New scripts are documented.
- A clean handoff explains what still requires user OAuth/macOS action.

## Workstream 7 — Introduction Engine Pass

Current state:
- `system/scripts/intro_engine.py` exists.
- Protocol exists: `system/protocols/P-013_intro_engine.md`.
- Core logic lives in `rb_core.find_intro_paths`.
- Current output is structural:
  - resolves person/company/free-text target,
  - finds insiders,
  - scores brokers by DRR/proximity/thread context,
  - applies hard-coded heuristic suppressions,
  - returns short reason lines.
- Gaps:
  - no draft intro message,
  - no reciprocity/rate-limit ledger,
  - no broker overuse protection,
  - no fit/trust/network-equity scorecard,
  - `intro_brokers.md` is empty placeholder,
  - heuristics are mirrored in code instead of loaded from `heuristics.md`,
  - weak persistence hooks from intro recommendation to loops/action drafts.

Relevant defect traces:
- `system/test_traces/2026-05-21-post-meeting-ri-governance-and-intro-orchestration-defect.md`
  - Intro recommendation lacked governed candidate scoring.
  - Need fit, trust maturity, reciprocity, operational readiness, and network-equity risk.
- `system/test_traces/2026-05-21-sarah-mcangus-warm-recruiter-introduction-canonical-persistence-defect.md`
  - Warm referral path was not persisted as relationship edge/trust-transfer event.

Sprint tasks:
1. Add governed intro scorecard fields to each candidate:
   - relationship strength to Todd,
   - relationship/proximity to target,
   - fit reason,
   - trust maturity,
   - reciprocity balance,
   - recent broker asks,
   - network-equity risk,
   - recommended posture: ask / nurture first / do not ask / direct outreach.
2. Add draft ask generation:
   - short message to broker in Todd’s voice,
   - explain why broker specifically,
   - give broker an easy opt-out,
   - avoid commission/free-intro-broker framing.
3. Add persistence hooks:
   - proposed loop: `intro_request_pending`,
   - proposed action draft,
   - source refs to target/broker evidence,
   - optional confirmation path before writes.
4. Populate or regenerate `intro_brokers.md`:
   - domain,
   - broker tier,
   - recent ask count,
   - known intro history,
   - protection/rate-limit note.
5. Load more heuristics from data:
   - parse known-pair and cluster suppression rules from `heuristics.md`,
   - reduce hard-coded drift in `rb_core.py`.
6. Add regression tests/fixtures for:
   - Toast / Bob Gibson,
   - Donnie Boivin SCN suppression,
   - Sarah McAngus warm recruiter referral path,
   - Ashwin / Dog Haus candidate scoring with Patrick, James Lewis, Ish Singh.

Acceptance:
- `python3 system/scripts/intro_engine.py "Toast" --json` returns governed candidates and draftable asks.
- `python3 system/scripts/intro_engine.py "Donnie Boivin" --include-suppressed --json` suppresses SCN/Hospitality Table candidates and explains why.
- Intro recommendations never produce spray-and-pray lists.
- A recommendation can be converted into a loop/action draft with confirmation.

## Suggested Sprint Order

1. Google OAuth client and token setup.
2. API daemon/tunnel health.
3. Full morning pipeline test.
4. Source-health cleanup and stale social/LinkedIn plan.
5. Repo cleanup / commit partitioning.
6. Introduction engine governed V1.

Rationale: email/calendar/API are reliability blockers. The intro engine is the product leap, but it should sit on a trustworthy source layer so the recommendations can carry evidence and freshness.

## Definition of Done

- Source health green or explicitly explained for tier-1 and Apple tier-2 sources.
- Custom GPT Actions retrieve the canonical Daily Brief through the named tunnel.
- Morning pipeline and task delivery checks pass.
- Claude has a clean repo state or intentional commit plan.
- Intro engine V1 produces governed, draft-ready, rate-limited introduction recommendations with persistence hooks.
