# Claude Handoff — Daily Brief Operating Layer Sprint

**Date:** 2026-05-21  
**Builder:** Codex + Todd  
**Sprint theme:** RB should identify and recommend, not behave like another dashboard.  
**Status:** core daily-brief operating-layer behavior implemented and smoke-tested; execution surfaces still pending.

## Product Philosophy

Todd's framing for this sprint:

> Restaurants do not need another dashboard. They need AI to identify and recommend. It should make people better.

Apply that to RB:

> Todd does not need another summary. RB should prepare the day, identify what matters, recommend the next move, and make action easy.

This is now canonical product behavior. Future work should reinforce this direction, not drift back into report generation.

## What Changed

The Daily Brief is now designed as an operational layer:

1. **Daily Prep Summary / Preparation Proof**
   - Shows which sources were scanned.
   - Reports RI found and "no actionable RI found."
   - Reports stale/missing sources instead of silently omitting them.

2. **Morning Command Center**
   - New canonical operating board after prep summary.
   - Surfaces top moves, meeting-prep queue, waiting-on state, loop risk, signal state, suppressed noise, and one decision needed.

3. **Action Affordances**
   - Canonical items now carry `action_options`.
   - Examples: `create_loop`, `draft_next_action`, `create_meeting_prep`, `create_waiting_loop`, `defer`, `mark_done`, `mark_irrelevant`, `monitor`.
   - This is the bridge from recommendation to operation.

4. **Calendar Prep / Deliverable Intelligence**
   - Calendar is not passive agenda text.
   - Events are scanned for prep needs, deliverable risk, known relationship attendees, unknown attendees, active-thread/company ties, and timing risk.
   - Daily Brief now includes **Meeting Prep & Deliverables**.
   - Smart task / loop creation should offer to create meeting prep for review and open prep loops.

5. **LinkedIn / Social Intelligence**
   - Where permissioned data exists, scan LinkedIn public posts, comments/reactions, own-post engagement, and LinkedIn messaging/interactions.
   - Public posts can feed RI and market/operator movement.
   - Messaging/interactions can feed RI, loop candidates, opportunities, and last-touch evidence.
   - Missing/stale/manual-only LinkedIn access must be reported in Daily Prep Summary.

6. **Waiting-On State**
   - Sent follow-ups are separated into overdue vs still-inside-response-window.
   - This prevents task anxiety and avoids nudging too early.

7. **Visible Ignore / Suppression**
   - The brief should show what RB ignored or suppressed.
   - "No action" is a valid recommendation when grounded.

8. **End-of-Day Closeout Offer**
   - Daily Brief now offers a closeout for what closed, slipped, is waiting, auto-resolved, and should roll to tomorrow.
   - Scheduled closeout automation is not yet built.

9. **5 AM Newspaper Delivery**
   - `rb-daily-briefing` automation is scheduled for 05:00 America/Chicago.
   - Completion email requirement is documented.
   - Email must point Todd to the RB operational layer, not Codex, Claude, local files, or implementation scaffolding.
   - Email transport and final user-facing operational URL remain pending.

## Primary Files Changed

Read these first:

```text
system/scripts/daily_brief.py
system/settings.json
system/protocols/P-001_daily_brief_regen.md
system/01_RB_TENETS.md
system/STATUS.md
system/CLAUDE_FOLLOWUP_MORNING_DELIVERY_PIPELINE.md
```

Important implemented anchors:

```text
daily_brief.py::_default_action_options
daily_brief.py::_build_command_center
daily_brief.py::_meeting_prep_items
daily_brief.py::build_daily_prep_summary
daily_brief.py::build_canonical_brief
daily_brief.py::render_today_md
```

## Validation Already Run

```bash
python3 system/scripts/daily_brief.py --smoke
python3 -m json.tool system/settings.json
python3 -B -c 'import ast, pathlib; ast.parse(pathlib.Path("system/scripts/daily_brief.py").read_text())'
python3 system/scripts/api_smoke_test.py
```

Latest result: all daily-brief smoke checks pass; all 27 API smoke endpoints pass.

## Known Boundaries / Do Not Overclaim

- Email transport is still `pending_transport`.
- Final RB operational-layer URL is still pending.
- LinkedIn live access is not implemented. RB scans LinkedIn/social where permissioned exports/captures/connectors/browser-fed inputs exist.
- LinkedIn messaging is surfaced in prep proof; full parser/RI operationalization remains pending.
- Meeting-prep candidates are surfaced; full auto-generated meeting-prep artifact workflow remains pending.
- End-of-day closeout is offered; dedicated closeout automation and passive verification rollup remain pending.

## Next Sprint Recommendation

Build the execution layer behind the new affordances.

Priority order:

1. **Loop Creation From Canonical Items**
   - Convert Daily Brief action items into tracked loops.
   - Include entity, source signal, action type, due date, verification method, confidence, and closure criteria.
   - Meeting-prep loops should be due before the meeting.

2. **Meeting Prep Artifact Generation**
   - Given a calendar event, generate a reviewable prep brief.
   - Include attendees, known relationship context, last touches, open loops, emails/messages/calls, LinkedIn/social signals, expected outcome, talking points, and follow-up likely needed.

3. **Passive Loop Verification**
   - Next-day scan should auto-close or propose closure when evidence appears.
   - Example: outbound email found after loop "email Dave" closes the loop or marks possible resolution.

4. **End-of-Day Closeout Automation**
   - Schedule or offer 16:30 local closeout.
   - Summarize closed, slipped, waiting, auto-resolved, carry-forward, and one tomorrow setup recommendation.

5. **RB Operational Layer Destination**
   - Publish the daily brief to a user-facing RB surface.
   - Completion email CTA must point there.
   - Do not point users to Codex, Claude, or filesystem paths.

6. **LinkedIn Messaging Parser**
   - Normalize LinkedIn message exports/captures.
   - Feed RI, loops, opportunity detection, and last-touch candidates.

7. **LinkedIn OAuth + Source Governance**
   - Add a user-authorized LinkedIn OAuth path where official LinkedIn API products/scopes permit it.
   - Do not request, store, or route LinkedIn passwords through RB.
   - Surface connected scopes, unavailable scopes, token health, and last successful sync in source readiness.
   - Keep browser/session capture as a user-directed, short-lived source for pages Todd actually views during normal LinkedIn use.
   - Keep periodic LinkedIn data exports as the durable baseline refresh path.
   - Daily Brief should distinguish `linkedin.oauth`, `linkedin.browser_session`, and `linkedin.export` evidence, including freshness and confidence.
   - Backlog reminder already created for Todd to download LinkedIn exports every other Friday at 09:00 America/Chicago.

## Acceptance Standard

The user should feel:

> RB prepared my day, checked the right sources, identified what matters, recommended what to do, and made the next step easy.

Not:

> ChatGPT wrote me another thoughtful summary.
