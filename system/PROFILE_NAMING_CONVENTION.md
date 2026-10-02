# RB Profile Naming Convention

Date: 2026-05-27

## Purpose

RB should support one user, several users, and enterprise deployments without hardcoding a single operator identity into the core system. Profile files should identify the account/person they describe, but engines and prompts should refer to them generically as user profiles.

## Canonical Profile Layout

Use this layout for new profile work:

```text
system/profiles/{profile_id}/profile.md
system/profiles/{profile_id}/preferences.yaml
system/profiles/{profile_id}/opportunity_context.yaml
system/profiles/{profile_id}/voice.md
system/profiles/{profile_id}/boundaries.md
system/profiles/{profile_id}/learning_log.jsonl
```

`profile_id` should be stable, lowercase, and human-readable:

```text
todd_vahlsing
jane_smith
enterprise_acme_cro
```

Do not use role-only names such as `ceo_profile` or `sales_profile`; roles change. Put role and company inside the profile content.

## File Responsibilities

- `profile.md` — career arc, domain expertise, durable strengths, known constraints, and current operating context.
- `preferences.yaml` — response mode, verbosity, time horizon, intro philosophy, risk tolerance, communication preferences, and default workflows.
- `opportunity_context.yaml` — target industries, target customers, target employers, product/service capabilities, job-search context, buying committees, and pain the user can credibly solve.
- `voice.md` — draft style, prohibited language, channel-specific tone, and examples.
- `boundaries.md` — conflict rules, engagement limits, privacy rules, intro boundaries, and employer/client constraints.
- `learning_log.jsonl` — dated observations about what worked, what the user ignored, corrected assumptions, and inferred preference changes.

## Legacy Compatibility

`system/00_TODD_PROFILE.md` is the current single-user seed profile. Treat it as a legacy alias for:

```text
system/profiles/todd_vahlsing/profile.md
```

New code, protocols, and prompts should prefer `profiles/{profile_id}/...`. Existing bootstrap references can keep reading `00_TODD_PROFILE.md` until the multi-profile loader exists.

## Product Rule

RB engines should not contain Todd-specific assumptions. They should read a selected profile, then use the profile to interpret signals, score fit, draft actions, and decide what to ignore.

The selected profile owns its data layer. Relationship graphs, uploaded files, market datasets, watchlists, ecosystem graphs, micro-topology graphs, derived indexes, and strategic memory created for one profile must not be reused as defaults or background knowledge for another profile. New users begin with empty/user-neutral templates and build their own graph from onboarding sources and ongoing usage.
