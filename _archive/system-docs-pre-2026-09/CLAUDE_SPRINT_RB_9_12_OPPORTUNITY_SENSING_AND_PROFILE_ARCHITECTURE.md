# Claude Sprint — RB 9.12 Opportunity Sensing And Profile Architecture

Date: 2026-05-27

## Sprint Thesis

RB should detect pain that the user can solve.

That pain may appear in relationship activity, industry intelligence, company news, social posts, earnings commentary, customer/vendor movement, or job postings. The product move is bigger than job search: the same sensing layer should surface sales opportunities, consulting opportunities, employment opportunities, partnership opportunities, content opportunities, and warm-intro opportunities.

## Product Principle

RB is not looking for generic market updates. It is looking for:

1. a real pain, priority, risk, transition, or investment signal;
2. a person, company, customer, employer, or ecosystem where the pain exists;
3. a reason the selected user or their product/service is credible against that pain;
4. a relationship path, action path, or watch posture.

## Priority 1 — Opportunity Sensing Object Model

Add an opportunity-sensing layer above raw market and relationship signals.

Proposed object:

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
    "evidence": ["..."]
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

## Priority 2 — Job Posting As Pain Signal

Treat job postings as intelligence, not only listings.

A job posting can reveal:

- a company priority;
- an unsolved operating problem;
- a team buildout;
- a new market push;
- a leadership gap;
- a product/customer segment investment;
- a potential sales motion if the user's product solves the posted pain;
- a potential employment motion if the user solves the pain personally.

Acceptance criteria:

- Given a job posting for a restaurant operator seeking a digital transformation leader, RB extracts the underlying pain, likely operating priority, buyer/employer context, and fit against the selected user profile.
- Given a job posting from a restaurant-tech vendor hiring enterprise account leadership, RB classifies it as both an employment signal and a vendor-growth signal.
- Given a posting that is unrelated to the selected user's capabilities, RB marks `opportunity_type=no_action` and explains why.

## Priority 3 — Profile-Driven Fit Scoring

Opportunity sensing must use a generic profile architecture, not Todd-specific assumptions.

Use the profile convention in:

```text
system/PROFILE_NAMING_CONVENTION.md
```

Required profile inputs:

- industries and adjacent industries;
- user capabilities and product/service capabilities;
- target customers and target employers;
- buyer personas and hiring personas;
- preferred opportunity types;
- no-go categories;
- relationship boundaries;
- geographic, compensation, travel, and work-style constraints when relevant;
- proof preferences and voice constraints.

Field naming rule:

- Prefer `why_this_matters_to_user` for new schemas.
- Treat legacy fields such as `why_this_matters_to_todd` as backward-compatible aliases only.
- New tests should fail if fresh code introduces person-specific schema fields.

Acceptance criteria:

- Engines read a selected `profile_id`.
- Fit scoring cites profile facts instead of hardcoded user assumptions.
- Daily Brief can say why a signal matters to the selected user, their product/service, or neither.
- The same event can produce different recommendations for two different profiles.

## Priority 4 — Daily Brief Surface

Add a concise Daily Brief section:

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
- If source freshness is weak, label the signal as `research_first` or `monitor`.
- Never invent a relationship path; unresolved paths are `no_known_path` or `unknown`.

## Priority 5 — Tests

Add regression tests for:

- market intelligence pain → sales opportunity;
- job posting pain → employment opportunity;
- job posting pain → sales opportunity;
- same signal scored differently for two profiles;
- no-action filtering for weak fit;
- relationship path confidence discipline;
- source freshness downgrade.
