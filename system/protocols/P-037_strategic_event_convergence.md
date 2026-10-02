---
id: P-037
title: Strategic event convergence — schema, matching protocol, and source freshness
script: |
  system/scripts/strategic_events.py (produces and persists strategic_events.json)
  system/scripts/market_source_feeds.py (public source feeder → evidence input)
  system/scripts/market_signals.py (enriches signals → evidence input)
  system/scripts/daily_brief.py (reads top events and themes for brief generation)
cache: system/strategic_events.json
reads:
  - system/inbox/market_signals_feed.jsonl
  - system/inbox/linkedin.daily_signals.jsonl
  - system/inbox/market_signals.json
writes:
  - system/strategic_events.json
  - system/.cache/market_source_feeds_health.json
trigger: |
  Embedded in refresh_sources.py --market run (before daily brief).
  Also triggered by: linkedin_freshness_bridge.py after LinkedIn ingestion,
  and on demand via strategic_events.py --in <signals.json> --confirm.
---

# P-037 — Strategic Event Convergence

## Purpose

RB should answer "how did you know this mattered before Todd pasted the LinkedIn post?" with proof:

- source(s) checked
- source freshness
- normalized signal row(s)
- strategic event id
- convergence score / lifecycle
- related event / theme
- relationship / action relevance
- daily brief placement decision

This protocol formalizes the event model, matching rules, proof stats, and source
freshness gates that make that answer verifiable.

---

## Strategic Event Store Schema

File: `system/strategic_events.json`

```json
{
  "schema": "rb_strategic_events_v1",
  "updated_at": "<ISO-8601 UTC>",
  "events": [<StrategicEvent>]
}
```

### StrategicEvent object

```json
{
  "event_id": "sev_<14-char sha1>",
  "event_key": "<namespace>:<slug>",
  "title": "<human-readable title>",
  "created_at": "<ISO-8601 UTC>",
  "last_seen_at": "<ISO-8601 UTC>",
  "evidence": [<Evidence>],
  "source_channels": ["<channel>", ...],
  "themes": ["<theme>", ...],
  "entities": {
    "companies": ["<Company name>", ...],
    "people": ["<Person name>", ...]
  },
  "lifecycle": "rumor_or_early_signal | early_signal | corroborated_signal | validated_signal",
  "confidence_score": 0.0,
  "convergence_level": "isolated | single_strong_source | multi_source | multi_source_validated",
  "thesis_alignment": {
    "aligned": true,
    "matched_theses": ["<thesis slug>", ...],
    "summary": "<one-sentence explanation>"
  },
  "related_event_ids": ["sev_...", ...],
  "recommended_actions": ["<action>", ...],
  "source_freshness_gate": "open | stale_blocked",
  "proof_stats": {
    "evidence_count": 0,
    "channels_distinct": 0,
    "last_fresh_evidence_at": "<ISO-8601 UTC | null>"
  }
}
```

### Evidence object

```json
{
  "evidence_id": "ev_<14-char sha1>",
  "event_key": "<namespace>:<slug>",
  "title": "<signal title>",
  "source_name": "<publication or channel name>",
  "source_channel": "<channel slug>",
  "source_type": "<vertical_trade | mainstream | public_company_primary | ...>",
  "url": "<canonical URL or null>",
  "published_at": "YYYY-MM-DD",
  "captured_at": "<ISO-8601 UTC>",
  "text_excerpt": "<up to 600 chars>",
  "entities": {"companies": [...], "people": [...]},
  "themes": ["<theme>", ...],
  "classification": {
    "signal_type": "<signal_type slug>",
    "strategic_relevance": "high | medium | low | none",
    "confidence": "high | medium | low"
  },
  "manual": false,
  "stale": false
}
```

---

## Event Key Namespace

Event keys use the format `<namespace>:<slug>`.

| Namespace | Meaning |
|---|---|
| `restaurant-ai` | Restaurant AI implementation, rollout, trust, litigation events |
| `restaurant-tech` | Broader restaurant technology vendor/operator events |
| `operator` | Specific operator strategy or pain signal |
| `payments` | Restaurant payments or acquiring events |

---

## Matching Protocol

### Company normalization

| Canonical | Aliases |
|---|---|
| `Yum Brands` | `Yum`, `YUM`, `Yum! Brands` |
| `Pizza Hut` | `Pizza Hut U.S.`, `PH` |
| `Dragontail` | `Dragontail Systems`, `Dragontail Technologies` |
| `Chaac Pizza Northeast` | `Chaac Pizza`, `CPNE` |
| `Global Payments` | `GlobalPay`, `Global Payments Inc.` |
| `Genius` | `Genius POS`, `Genius by Global Payments` |
| `McDonald's` | `McDonalds`, `MCD` |
| `Starbucks` | `SBUX`, `Starbucks Coffee` |
| `Toast` | `Toast POS`, `Toast Inc.` |

### Topic tag normalization

| Theme slug | Trigger concepts |
|---|---|
| `operational_ai_trust` | ai, automation, computer vision, rollout, accuracy, trust, reliability |
| `franchisee_trust_and_governance` | franchisee, franchisor, lawsuit, compliance, governance |
| `friday_night_survivability` | rollout, workflow, accuracy, operations, survival, Friday night |
| `market_signal` | (fallback) |

### URL canonicalization

- Strip query strings except `?id=`, `?article=`, `?p=`, `?postid=`
- Strip trailing slash
- Lowercase scheme and host
- Collapse `//` in path

### Source-channel deduplication

`evidence_id = sha1(event_key | source_channel | canonical_url | text[:300])`

Two pieces of evidence from the same channel about the same event are counted as one.
Evidence from different channels counts as two distinct channels.

### Recurring theme clustering

Events sharing ≥1 theme are linked via `related_event_ids` after each merge pass.

---

## Lifecycle and Convergence Rules

| Lifecycle | Condition |
|---|---|
| `rumor_or_early_signal` | Single channel, confidence_score < 0.65 |
| `early_signal` | Single channel, confidence_score ≥ 0.65 |
| `corroborated_signal` | ≥ 2 distinct channels |
| `validated_signal` | ≥ 2 distinct channels AND confidence_score ≥ 0.75 |

Isolated signals are captured but suppressed from Daily Brief surfacing.

### Source weight table

| Channel | Weight |
|---|---|
| `restaurant_trade_news` | 0.24 |
| `enterprise_restaurant_technology_news` | 0.24 |
| `legal_business_news` | 0.22 |
| `earnings_call` | 0.20 |
| `company_press` | 0.18 |
| `analyst_commentary` | 0.18 |
| `linkedin_social` | 0.16 |
| `network_conversation` | 0.16 |
| `podcast_interview` | 0.14 |
| `user_uploaded_artifact` | 0.12 |

Convergence bonus: +0.18 when ≥ 2 channels present.
Cross-channel bonus: +0.10 when `legal_business_news` + `linkedin_social` both present.
Thesis bonus: +0.08 when `operational_ai_trust` theme present.

---

## Source Freshness Gates

| Channel | Freshness window |
|---|---|
| `restaurant_trade_news` | 48 hours |
| `enterprise_restaurant_technology_news` | 48 hours |
| `legal_business_news` | 72 hours |
| `linkedin_social` | 24 hours |
| `company_press` | 72 hours |
| All others | 48 hours |

**FG-1**: A stale channel may not be the sole channel promoting to `validated_signal`. Cross-channel validation requires at least one fresh channel.

**FG-2**: Manually-provided artifacts may amplify confidence but may not change `source_channel` classification to `system_detected`.

**FG-3**: Events with only stale evidence are flagged `source_freshness_gate: "stale_blocked"`. Daily Brief must surface this flag.

**FG-4**: `validated_signal` requires at least one evidence piece published within the last 14 days.

---

## Proof Stats

Every `merge_signals()` call returns:

```json
{
  "captured": 0,
  "recorded": 0,
  "updated": 0,
  "deduped": 0,
  "stale_ignored": 0,
  "isolated_suppressed": 0,
  "validated_surfaced": 0,
  "ignored": 0
}
```

---

## Daily Brief Integration

1. Include only `corroborated_signal` or `validated_signal` events.
2. Prefix "Multiple-source convergence detected" only when `convergence_level` is `multi_source` or `multi_source_validated`.
3. Mark stale_blocked events with `[source freshness gap]` — do not omit them.
4. Never surface isolated LinkedIn commentary as proactive discovery.

---

## Definition of Done

RB must be able to answer: "How did you know this mattered before Todd pasted the LinkedIn post?"

| Proof field | Where it lives |
|---|---|
| Source(s) checked | `health.sources[].status` in market_source_feeds_health.json |
| Source freshness | `evidence[].captured_at` vs freshness window |
| Normalized signal row(s) | `evidence[]` in strategic_events.json |
| Strategic event ID | `event_id` |
| Convergence score / lifecycle | `confidence_score`, `lifecycle`, `convergence_level` |
| Related event / theme | `related_event_ids`, `themes` |
| Relationship / action relevance | `ri_assessment` block in market_signals.py output (P-036) |
| Daily Brief placement decision | `lifecycle` gate + Brief section inclusion logic |
