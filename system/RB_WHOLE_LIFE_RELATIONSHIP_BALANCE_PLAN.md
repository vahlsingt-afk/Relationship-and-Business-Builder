# RB Whole-Life Relationship Balance Plan

Status: planned future add-on after Todd's RB 9.0 core is stabilized.

## Purpose

RB should help the user stay faithful to the relationships and commitments they say matter. That starts with professional relationship intelligence, but the same Chief-of-Staff pattern can optionally support family, friendship, service, community, recreation, vacation, and wellbeing commitments.

This must remain opt-in. The system should be helpful, calm, and non-judgmental, not intrusive.

## Core Principle

RB is not a generic to-do list. It is a relationship-aware operating system.

User-entered to-dos are high-signal inputs because they reflect explicit intent. RB should classify and prioritize them, especially when they are relationally relevant, tied to opportunity momentum, or connected to user-stated life-balance priorities.

## User-Entered To-Dos

When the user enters a task, RB should classify it before deciding whether it belongs in the operating brief, a loop, a waiting-on state, or a suppressed backlog.

Planned classifications:

- `relationship_obligation`
- `opportunity_obligation`
- `meeting_prep`
- `personal_relationship_care`
- `service_commitment`
- `church_or_community_commitment`
- `health_wellbeing_prompt`
- `recreation_recovery`
- `vacation_planning`
- `admin_task`
- `suppress`

Promotion rules:

- Prioritize user-entered tasks over inferred tasks.
- Promote tasks that affect trust, relationship momentum, opportunity movement, preparation, or user-defined wellbeing.
- Keep generic admin work out of the daily brief unless the user explicitly marks it as important or it blocks a relationship/opportunity.
- Track counterparty, due window, source, and consequence when available.
- Use `act_today`, `monitor`, `ask_todd`, or `ignore` as the operating disposition.

## Optional Life Domains

The future setting should be disabled by default:

```json
{
  "whole_life_relationship_balance": {
    "enabled": false,
    "domains": {
      "family": false,
      "friends": false,
      "service": false,
      "church_or_community": false,
      "health_wellbeing": false,
      "recreation": false,
      "vacation": false
    }
  }
}
```

Example use cases if enabled:

- Family: "You asked RB to remind you if more than three weeks pass without calling your mom."
- Kids: "You wanted to protect one intentional touchpoint with each kid this week."
- Service/church/community: "You committed to follow up with the volunteer coordinator by Friday."
- Recreation/recovery: "You said Wednesday evenings should stay protected for recreation unless there is a true emergency."
- Vacation: "You mentioned needing to schedule the summer trip before rates climb."
- Wellbeing: "You asked RB to watch whether travel and work are crowding out workouts or rest."

## Guardrails

- Opt-in only.
- User controls domains, cadence, and wording.
- Non-judgmental language only.
- No guilt, shame, or moral scoring.
- No medical, clinical, therapy, or diagnostic advice.
- Do not infer sensitive family, religious, health, or emotional obligations without explicit user input.
- Never surface personal-life prompts in professional contexts unless the user has explicitly allowed that surface.
- Make it easy to snooze, disable, or narrow a domain.

## CoS Output Pattern

Whole-life prompts should use the same canonical CoS discipline:

1. Short answer.
2. Why it matters.
3. Proof or source.
4. Recommended next move.
5. Disposition.

Example:

```text
Personal relationship care detected - RB proposed the following:

- Priority: Call Mom this week.
- Why it matters: You set a preferred cadence of every 2-3 weeks, and the last recorded touch is 24 days ago.
- Proof: user-defined family cadence; last_touch: 2026-04-26.
- Next move: 15-minute call before Friday.
- Status: proposed, pending confirmation.
```

## Onboarding Integration

During onboarding, RB should ask whether the user wants the system limited to professional relationships or expanded into optional life-balance domains.

Recommended onboarding wording:

```text
RB can stay focused only on professional network intelligence, or you can optionally allow it to help you protect personal relationships and life-balance commitments. You control which domains are on.
```

## Development Sequence

1. Add settings schema for user task intake and whole-life relationship balance.
2. Add task classification model for user-entered tasks.
3. Add per-domain opt-in settings and cadence preferences.
4. Add canonical rendering for user-entered priority tasks.
5. Add daily brief section for personal/life-balance prompts only when enabled.
6. Add privacy and surface controls.
7. Add tests proving disabled-by-default behavior.

## Success Criteria

RB succeeds when it helps the user stay on track with the commitments they actually care about, without increasing noise or creating emotional pressure.

The user should feel:

- "RB noticed what I told it mattered."
- "RB is helping me keep faith with people, not just chase opportunities."
- "RB is quiet when nothing needs my attention."
