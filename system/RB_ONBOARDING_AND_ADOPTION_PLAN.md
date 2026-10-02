# RB Onboarding and Adoption Plan

**Status:** Future development after Todd's system is stabilized and the reusable framework is ready for other users.

## Why This Exists

RB will not be adopted just because it is powerful. A new user has to trust it quickly, understand where to begin, and see useful relationship intelligence before the setup burden feels larger than the value.

The onboarding experience should answer five questions:

1. What is RB?
2. What does it need from me?
3. What does it already know?
4. What should I do first?
5. Why should I trust the recommendation?

## Product Goal

Create a mostly automated install and guided onboarding path that turns a new user from "I have a contact mess" into "RB is helping me manage my relationships, day, schedule, and opportunities" with minimal manual setup.

The first session should not feel like configuring software. It should feel like a capable Chief of Staff asking the right few questions, checking the right sources, and producing an early win.

## Target Onboarding Flow

### 1. Install and Environment Check

Goal: get RB running with as little technical friction as possible.

Needed:

- One-command local install or guided installer.
- Dependency check for Python, API server, local workspace, permissions, and optional connectors.
- Clear success/failure messages.
- Local privacy explanation: what stays local, what is connected, what is written.
- First-run health check:
  - API reachable
  - settings readable
  - baseline present or ready to create
  - inbox/source folders present
  - write permissions confirmed
  - optional calendar/email/social connectors configured or skipped
  - every expected Gmail and Google Calendar identity listed in `system/inbox/accounts.yaml`
  - source-readiness result for each expected feed (`calendar`, `email`, `email_sent`)
  - default business-calendar designation or explicit "no business calendar" choice
  - calendar visibility test: create/read or list-events proof for each enabled calendar account

Future artifact:

```text
system/onboarding/install_check.py
system/onboarding/first_run_wizard.py
system/onboarding/README_FOR_NEW_USERS.md
```

Multi-account setup must be explicit. RB should ask which accounts carry real meetings and relationship mail, validate each connected account, and show the user which calendars are connected, stale, missing, or intentionally skipped. A missing work calendar is not a harmless setup detail; it directly degrades meeting prep.

### 2. User Profile Intake

Goal: learn who the user is before interpreting their network.

RB should ask for or infer:

- Name and preferred name.
- Current role and company.
- Career context: employed, consulting, job search, founder, operator, advisor, investor, other.
- Industry and sub-industry.
- Core relationship goals:
  - job search
  - sales pipeline
  - advisory/consulting work
  - fundraising
  - hiring/recruiting
  - partnerships
  - community/chapter building
  - personal relationship stewardship
- Relationship style and boundaries.
- Intro philosophy: conservative, balanced, aggressive.
- Communication preferences.
- Preferred CoS response mode:
  - expert
  - intermediate
  - beginner
- Default verbosity.

Output:

```text
system/profiles/{profile_id}/profile.md
system/profiles/{profile_id}/preferences.yaml
system/settings.json
```

Profile naming should follow `system/PROFILE_NAMING_CONVENTION.md`. The current Todd profile is a seed/single-user instance, not the naming model for enterprise deployments.

### 3. Industry and Opportunity Context

Goal: make RB domain-aware instead of generic.

RB should learn:

- User's industry.
- Target companies, customers, employers, markets, or communities.
- Important vendors/platforms/competitors.
- Relevant job titles and buyer personas.
- Pain the user or the user's product/service can credibly solve.
- Job-posting patterns that reveal hiring pain, customer pain, or sales opportunity.
- Industry-specific sources to monitor.
- Terms of art and language that matter.
- Things that look important but are usually noise.

For Todd, this is restaurant tech, franchise operations, payments/POS, operator adoption, enterprise accounts, and consulting/advisory boundaries. For another user, this layer must be rebuilt around their domain.

Todd's private data sources are not onboarding defaults. The McDonald's NSN workbook, Technomic Top 1500 files, Todd's relationship data, and any derived ecosystem graphs/indexes from those sources stay in Todd's instance. A new user receives the same ingestion and graph-building capability, not Todd's data.

Output:

```text
system/profiles/{profile_id}/opportunity_context.yaml
system/watchlists/{profile_id}/industry_watchlist.md
system/watchlists/{profile_id}/target_employers.md
system/watchlists/{profile_id}/target_customers.md
system/heuristics.md
```

### 4. Baseline Creation

Goal: establish the first trustworthy relationship graph.

Sources may include:

- LinkedIn export.
- Google/Apple contacts.
- Email metadata or selected threads.
- Calendar history.
- CRM export.
- Manual CSV.
- Existing notes.
- User-provided top relationships.

The baseline process should:

- Deduplicate contacts.
- Identify likely relationship classes.
- Separate evidence-derived signal from user-confirmed trust.
- Propose top RC/LKI/LMI candidates for review.
- Ask the user to confirm only the most important ambiguous cases.
- Create initial circles and active threads.
- Produce a "what RB thinks it knows" report.

Important trust rule:

RB should not pretend the first baseline is perfect. It should say what was detected, what was inferred, what needs confirmation, and what it is deliberately leaving alone.

Output:

```text
system/baseline_index.json
system/cards/
system/circles/
system/network_map.md
system/MANIFEST.md
```

### 5. First Value Moment

Goal: produce immediate utility before asking the user to invest more time.

Within the first session, RB should generate one of:

- "What matters today"
- "Who deserves attention"
- "Which opportunities are active"
- "What should you ignore"
- "What is missing from the relationship graph"
- "Three people to reconnect with and why"
- "One intro you should consider, plus risk"

The first value moment must be proof-backed and modest. It should avoid overclaiming.

Recommended first response shape:

```text
Short answer:
RB has enough initial signal to make a small recommendation, but not enough to be fully trusted yet.

What matters:
- [act_today] <person/thread> — <why now> (<grounding>; confidence <level>)

Proof:
- <source/date/count>
- <what is missing or stale>

Next move:
- Confirm whether <relationship/person/thread> is actually important.
```

### 6. Guided Tour

Goal: teach the user how to get value without making them read architecture docs.

The guided tour should be conversational and task-based:

1. Ask "what is important today?"
2. Ask "who should I focus on this week?"
3. Ask "what am I missing?"
4. Ask "show me one relationship and why RB ranks it this way."
5. Ask "what should I ignore?"
6. Confirm or correct one relationship.
7. Create or close one loop.

Each step should show:

- what RB checked
- what it found
- what it recommends
- what proof supports it
- what the user can correct

Future artifact:

```text
system/onboarding/GUIDED_TOUR.md
```

### 7. Best Practices Document

Goal: help users keep RB useful after setup.

Topics:

- How to ask CoS questions.
- How to add relationship intelligence.
- How to correct RB without over-explaining.
- How to use loops.
- How to review active threads.
- How to use "what should I ignore?"
- How to interpret grounding labels.
- How often to refresh sources.
- What not to expect from RB.
- How to avoid turning RB into a contact database.

Future artifact:

```text
system/onboarding/BEST_PRACTICES.md
```

## Trust-Building Principles

RB should earn trust by being specific, bounded, and auditable.

Non-negotiables:

- Say what was checked.
- Say what was not checked.
- Say what is stale.
- Separate facts from inference.
- Use grounding labels.
- Ask small confirmation questions.
- Show proof before asking the user to act.
- Do not over-rank relationships from thin evidence.
- Do not make the user feel blamed for incomplete data.

The right user feeling is:

```text
"This system is already useful, and I understand why it thinks what it thinks."
```

## Beginner / Intermediate / Expert Onboarding

Tie onboarding to `operator_experience_level`.

- **Beginner:** slower tour, more explanation, trust-building language, more "here is what RB checked."
- **Intermediate:** default guided tour, concise explanations, proof shown inline.
- **Expert:** fast setup, terse proof, minimal explanation, direct controls.

This should affect onboarding copy and examples, not the underlying facts or recommendations.

## Minimum Viable Onboarding

After Todd's system stabilizes, the first reusable onboarding milestone should include:

1. `BOOTSTRAP.md` rewrite for a new user.
2. First-run checklist.
3. User profile intake template.
4. Baseline import checklist.
5. Guided tour doc.
6. Best practices doc.
7. A generated first-value report.
8. A smoke test proving the new user's RB can answer:
   - "what is important today?"
   - "who should I focus on?"
   - "what am I missing?"
   - "what should I ignore?"

## Development Sequence

Do not build this before Todd's RB is stable. Use Todd's system as the reference implementation first.

Recommended order:

1. Stabilize Todd's daily CoS response and RI persistence.
2. Stabilize source refresh and baseline projection sync.
3. Extract generic configuration from Todd-specific assumptions.
4. Create reusable `00_USER_PROFILE.md` and industry-watchlist templates.
5. Build first-run installer/checker.
6. Build baseline import/review wizard.
7. Create guided tour and best-practices docs.
8. Add onboarding smoke tests.
9. Test with one non-Todd sample user profile.

## Open Product Questions

- Should RB support a fully local no-connector mode for privacy-first users?
- Which source should be the easiest first import: LinkedIn, contacts, calendar, or CSV?
- How much baseline review can be automated before trust drops?
- Should guided onboarding happen in ChatGPT, Codex, a CLI, or a small local web UI?
- What is the minimum relationship graph size before RB can produce a useful first recommendation?
- How should RB explain privacy and local storage in plain language?
- What should RB do when a new user has no clean exports?
