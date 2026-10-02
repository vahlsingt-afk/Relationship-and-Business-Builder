# Claude Handoff — RB 9.12 Opportunity Sensing + Profile Architecture

Date: 2026-05-27

## Why This Sprint Exists

RB is evolving from relationship tracking and daily brief generation into a Chief of Staff operating layer. The next product leap is opportunity sensing:

> RB detects pain, priority, risk, transition, or investment signals that the selected user or their product/service can credibly solve.

This must work for job seekers, salespeople, consultants, founders, advisors, and enterprise users. The same signal may be a job opportunity for one profile, a sales opportunity for another, a consulting wedge for a third, and no-action noise for a fourth.

## Current Codex Progress

Codex has already added the product framing and profile architecture docs:

- `system/PROFILE_NAMING_CONVENTION.md`
- `system/CLAUDE_SPRINT_RB_9_12_OPPORTUNITY_SENSING_AND_PROFILE_ARCHITECTURE.md`

Codex also updated profile references in:

- `system/RB_ONBOARDING_AND_ADOPTION_PLAN.md`
- `system/CLAUDE_DEVELOPMENT_MAP.md`
- `system/01_RB_TENETS.md`
- `system/MANIFEST.md`
- `system/STATUS.md`
- `system/ARCHITECTURE.md`
- `system/BOOTSTRAP.md`
- `system/WHAT_PERSISTS.md`
- `system/protocols/P-001_daily_brief_regen.md`
- `system/protocols/P-002_linkedin_ingest.md`
- `system/heuristics.md`
- `system/scripts/daily_brief.py`

Validation after these doc/code-touch updates:

```bash
python3 system/scripts/daily_brief.py --smoke
python3 -m pytest system/tests/test_market_source_feeds.py system/tests/test_linkedin_freshness_bridge.py -q
```

Both passed.

## Required Read Set

Read these first:

1. `system/PROFILE_NAMING_CONVENTION.md`
2. `system/CLAUDE_SPRINT_RB_9_12_OPPORTUNITY_SENSING_AND_PROFILE_ARCHITECTURE.md`
3. `system/RB_ONBOARDING_AND_ADOPTION_PLAN.md`
4. `system/CLAUDE_DEVELOPMENT_MAP.md`
5. `system/scripts/market_source_feeds.py`
6. `system/scripts/market_signals.py`
7. `system/scripts/strategic_events.py`
8. `system/scripts/opportunity_intake.py`
9. `system/scripts/daily_brief.py`

Also scan:

- `system/restaurant_tech_watchlist.md`
- `system/circles/target-employers.md`
- `system/circles/target-restaurants-tech-leaders.md`
- `system/SCHEMAS.md`

## Product Rule: Onboarding Is A Dependency

RB cannot score opportunity fit without knowing the user.

First-run onboarding must create or select a user profile before opportunity sensing makes strong claims.

Canonical profile layout for new work:

```text
system/profiles/{profile_id}/profile.md
system/profiles/{profile_id}/preferences.yaml
system/profiles/{profile_id}/opportunity_context.yaml
system/profiles/{profile_id}/voice.md
system/profiles/{profile_id}/boundaries.md
system/profiles/{profile_id}/learning_log.jsonl
```

`system/00_TODD_PROFILE.md` is a legacy single-user seed. Do not add new Todd-specific assumptions to engines.

New schemas should prefer:

```text
why_this_matters_to_user
```

Treat legacy fields such as `why_this_matters_to_todd` as backward-compatible aliases only.

## Sprint Deliverables

### 1. Selected Profile Loader

Build a small profile loader that can:

- resolve the active `profile_id`;
- read the profile docs above;
- fall back to `system/00_TODD_PROFILE.md` in legacy single-user mode;
- expose selected profile facts to opportunity sensing and daily brief rendering.

Suggested file:

```text
system/scripts/user_profile.py
```

Minimum output shape:

```json
{
  "profile_id": "todd_vahlsing",
  "source": "profiles_dir|legacy_seed",
  "facts": {},
  "capabilities": [],
  "product_service_capabilities": [],
  "target_customers": [],
  "target_employers": [],
  "target_industries": [],
  "adjacent_industries": [],
  "preferred_opportunity_types": [],
  "no_go_categories": [],
  "constraints": {},
  "voice_constraints": [],
  "confidence": "high|medium|low"
}
```

### 2. First-Run User Discovery / Profile Bootstrap

Design or implement the first-run onboarding path that gets to know the user.

It should collect enough to create:

- identity and current context;
- career/business goals;
- industries and adjacent markets;
- products/services/capabilities;
- target customers/employers;
- pain the user can credibly solve;
- opportunity types the user wants surfaced;
- communication preferences;
- relationship and intro boundaries;
- privacy/conflict constraints.

This can begin as a CLI or markdown/template workflow if a UI is too large for this sprint.

Suggested files:

```text
system/onboarding/profile_bootstrap.py
system/onboarding/profile_intake_template.md
system/tests/test_user_profile.py
```

### 3. Opportunity Sensing Object Model

Add an opportunity-sensing layer above raw market/news/social/job signals.

Suggested file:

```text
system/scripts/opportunity_sensing.py
```

Core object:

```json
{
  "opportunity_signal_id": "...",
  "detected_at": "ISO datetime",
  "source": {
    "source_type": "market_news|job_posting|linkedin|email|calendar|earnings_call|press_release|company_blog|manual_note",
    "source_name": "...",
    "url": "...",
    "freshness": "fresh|stale|manual_context|unknown"
  },
  "pain": {
    "summary": "...",
    "pain_type": "growth|cost_pressure|implementation_failure|leadership_gap|customer_churn|operational_complexity|digital_transformation|compliance|market_expansion|turnaround|hiring_need|vendor_gap",
    "evidence": []
  },
  "subject": {
    "company": "...",
    "people": [],
    "industry": "...",
    "customer_or_employer": "customer|employer|partner|vendor|unknown"
  },
  "fit": {
    "user_capability_match": "high|medium|low|none",
    "product_service_match": "high|medium|low|none",
    "matched_profile_capabilities": [],
    "why_user_can_help": "..."
  },
  "opportunity_type": "sales|job|consulting|partnership|intro|content|research|no_action",
  "relationship_path": {
    "status": "warm_path|possible_path|no_known_path|unknown",
    "contacts": [],
    "broker_candidates": []
  },
  "recommended_posture": "act_today|nurture|monitor|research_first|ignore",
  "confidence": "high|medium|low",
  "proof_stats": {
    "sources_reviewed": 0,
    "matching_profile_facts": 0,
    "matching_relationship_paths": 0,
    "deduped_signals": 0
  }
}
```

### 4. Job Postings As Intelligence

Job postings are first-class intelligence sources.

They can reveal:

- employment opportunity for the user;
- sales opportunity into the hiring company;
- customer pain;
- team buildout;
- budget or transformation priority;
- leadership gap;
- new product/customer segment investment;
- weak signal of strategy or market movement.

RB should not treat a job posting as merely a job listing. It should extract pain and score fit against the selected user profile.

### 5. Daily Brief Surface

Add or prepare a concise section:

```text
Opportunity Sensing

[act_today] Company / signal
Pain detected: ...
Why the user can help: ...
Opportunity type: sales/job/consulting/partnership/content
Relationship path: ...
Recommended move: ...
Grounding: ...
Confidence: ...
```

Rules:

- Do not surface every job posting or market update.
- Prefer multi-source convergence, known relationship paths, high profile fit, or time-sensitive movement.
- If source freshness is weak, use `research_first` or `monitor`.
- Never invent a relationship path.
- If no credible fit exists, classify as `no_action` or `ignore`.

## Privacy And Profile-Learning Rules

Profile learning must be disciplined:

- Separate user-stated facts from inferred preferences.
- Store hypotheses with confidence.
- Do not infer sensitive facts without evidence.
- Ask only consequential questions.
- Make profile edits auditable.
- Do not overwrite profile facts silently.
- Keep enterprise/multi-profile separation clean.

## Acceptance Tests

Add tests for:

1. Market intelligence pain -> sales opportunity.
2. Market intelligence pain -> consulting opportunity.
3. Job posting -> employment opportunity.
4. Job posting -> sales opportunity.
5. Same signal scored differently for two profiles.
6. Weak-fit signal -> `no_action` or `ignore`.
7. Source freshness downgrade -> `research_first` or `monitor`.
8. Relationship path confidence discipline.
9. Legacy `00_TODD_PROFILE.md` fallback works.
10. New code does not introduce fresh person-specific schema fields such as `why_this_matters_to_todd`.

## Example Scenarios

### Scenario A — Sales Opportunity

Input:

Restaurant operator announces store closures, margin pressure, and a technology modernization program.

Expected:

- Pain: cost pressure / operational complexity / turnaround.
- Opportunity type: `sales` or `consulting`, depending on profile.
- Fit: high only if the selected profile has relevant capabilities.
- Posture: `research_first` unless warm relationship path exists.

### Scenario B — Job Posting As Employment Signal

Input:

Restaurant-tech vendor posts VP Enterprise Accounts role for national chain expansion.

Expected:

- Pain: growth / enterprise GTM / customer expansion.
- Opportunity type: `job`.
- Fit: profile-dependent.
- Relationship path: known contacts/brokers if present, otherwise `no_known_path`.

### Scenario C — Job Posting As Sales Signal

Input:

Target company posts for Director of Digital Transformation, mentioning fragmented systems, loyalty data, and POS modernization.

Expected:

- Pain: digital transformation / vendor gap / operational complexity.
- Opportunity type: `sales` if the user's product/service can solve it; `job` if the user fits the role; possibly both.
- Recommended posture should reflect the stronger path.

### Scenario D — Multi-Profile Difference

Same signal:

Restaurant operator invests in AI drive-thru modernization.

Expected:

- Profile A, job seeker with restaurant-tech GTM background: possible job/networking signal.
- Profile B, SaaS seller with drive-thru product: sales opportunity.
- Profile C, advisor/consultant: consulting/content opportunity.
- Weak-fit profile: monitor or ignore.

## Things Not To Do

- Do not build unsupported scraping.
- Do not make LinkedIn the only discovery path.
- Do not hardcode Todd-specific assumptions into new engines.
- Do not surface generic news without pain, fit, and action logic.
- Do not create relationship mutations from public market/news signals alone.
- Do not claim a warm relationship path without evidence.

## Suggested Implementation Order

1. `user_profile.py` loader with legacy fallback.
2. Profile bootstrap template / minimal CLI.
3. `opportunity_sensing.py` pure functions and fixtures.
4. Job-posting fixture normalization.
5. Multi-profile tests.
6. Daily Brief section integration.
7. OpenAPI/GPT schema update only after the internal object model is stable.

## Done Definition

RB 9.12 is complete when a fresh user profile can be created or loaded, opportunity signals can be scored against that profile, job postings are treated as pain intelligence, and the same source event produces profile-specific recommendations with proof stats, confidence, posture, and relationship-path discipline.
