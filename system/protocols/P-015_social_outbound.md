---
id: P-015
title: Social outbound — your posts + engagement
script: system/scripts/social_outbound.py
cache: system/.cache/social_outbound.json
reads:
  - system/inbox/social.own_posts.json
  - system/inbox/social.engagement.json
  - system/baseline_index.json
  - system/active_threads.yaml
writes: []
inputs:
  - name: recent_days
    description: Window for "recent posts" + engagement silence (default 30).
    required: false
trigger: every daily brief run; on-demand when operator asks what to post or how recent posts performed
---

# P-015 — Social outbound

## Purpose

Track your own posts and the engagement they receive from your network. Surface two products on top: (1) a debrief on how recent posts performed in *relationship terms* not vanity-metric terms, and (2) ranked recommendations for what to post next, grounded in active threads + topics that historically engaged specific contacts.

This is the chief-of-staff differentiator versus Buffer / Taplio / AuthoredUp: not "what to post for max likes" but "what to post to maintain the relationships you care about and advance the work you're actually doing."

## Why the framing matters

A post that gets 200 likes from strangers is worth less than a post that gets a single comment from Bob Gibson during an active hiring conversation at Toast. Generic LinkedIn analytics tools can't distinguish. RB can — every engager gets matched to baseline; engagement is DRR-weighted; comments are worth more than likes; recency decays the score; active-thread overlap multiplies.

## Data sources

`system/inbox/social.own_posts.json` and `system/inbox/social.engagement.json`. See `system/inbox/README.md` for the canonical schemas. Manual paste via `mutations.py my-post-add` and `mutations.py engagement-add` is the V0 path. Browser-extension or Sales Navigator API ingestion is V1.

## What the overlay produces

- **recent_posts** — your posts in the window, with active-thread company tags so you see which posts touched in-flight work.
- **engagement_by_contact** — every baseline contact who engaged, ranked by composite (DRR-weighted, recency-decayed, weighted by engagement type). Bob's comment outweighs 50 stranger likes.
- **engagement_silence** — contacts who engaged historically but have gone quiet in the window. Engagement-as-dormancy: a parallel cooling signal to `last_touch`.
- **topic_engagement_map** — which post topics engaged which contacts. Builds up a heatmap of who cares about what.
- **active_thread_engagement** — engagement events on posts that touched an active-thread company.

## What the recommendation engine produces

Ranked list with three pattern types:

1. **Topics that historically engaged specific contacts who've gone quiet.** Highest signal for re-warming a specific relationship.
2. **Posts about active-thread companies that haven't had recent engagement.** Re-surface in-flight work.
3. **Topics with strong engagement history across multiple top contacts.** Do more of what works.

Each recommendation cites which contacts it would warm and which threads it touches.

## Operations

```bash
# Add an own-post:
python3 system/scripts/mutations.py my-post-add \
    --text "..." --posted-at 2026-05-13 \
    --topics restaurant-tech operator-pain \
    --url "https://www.linkedin.com/posts/..." \
    --likes 47 --comments 12 --shares 3

# Add engagement:
python3 system/scripts/mutations.py engagement-add \
    --post-id "https://www.linkedin.com/posts/..." \
    --type comment \
    --engager-name "Bruce Sellnow" \
    --engager-url "https://www.linkedin.com/in/bruce-sellnow" \
    --at "2026-05-13T11:30:00-05:00"

# Run the overlay:
python3 system/scripts/social_outbound.py

# Run the recommendation engine:
python3 system/scripts/social_outbound.py --recs

# Cache for downstream:
python3 system/scripts/social_outbound.py --cache
python3 system/scripts/social_outbound.py --recs --cache
```

Or via MCP (`rb.social_outbound_overlay`, `rb.post_recommendations`, `rb.my_post_add`, `rb.engagement_add`) / HTTP (`GET /social_outbound_overlay`, `GET /post_recommendations`, `POST /my_posts`, `POST /engagement`).

## Composite scoring formula (V0)

For an engager E on post P at time t:

    weight_by_type = {like: 1, comment: 5, share: 3}
    recency_mult   = max(0.2, 1.0 - age_days / (2 * recent_days_window))
    event_score    = weight_by_type[type] * recency_mult
    composite      = total_event_score * (DRR(E) / 50.0)

DRR normalization at 50 means contacts with DRR ≥ 50 amplify the composite; lower contacts attenuate. Roughly aligns inner-tier engagement to count for 2-3× what LMI engagement counts for.

The formula is intentionally simple for V0. The weights and recency curve should be validated against operator intuition; tune in `rb_core.social_outbound_overlay()`.

## First validation run (2026-05-15)

Two seed posts (multi-unit operator post + OTP3 cohort post) plus 5 engagement events (Bruce, Chason, Dave, John). The overlay correctly surfaced:

- Engagement-by-contact ranked Bruce highest (composite 11.4 — comment on operator post + like on cohort post)
- Topic-engagement map identified `leadership` / `relationships` / `cohort` as a cluster (3 of OTP3 engaged the cohort post) and `restaurant-tech` / `operator-pain` / `ops-platforms` as another (Bruce + Chason engaged the operator post)
- Recommendation engine produced 7 ranked items: post about Global Payments / Matrix / LongFi / Toast to activate dormant threads (HIGH), and lean into proven topics like `leadership` (MEDIUM)

## Failure modes

- **Engager not in baseline.** Skipped silently. To surface, add the engager via `mutations.py contact-add` first, then re-run. The overlay does NOT auto-promote unmatched engagers — that's an operator decision.
- **Topics are operator-tagged.** If you forget to tag topics on a post, the topic-engagement map can't surface that post's signal. Discipline: tag every post on add.
- **Post id collisions.** Default id is `post_url` if provided, else generated. If you re-post the same URL, the existing entry is overwritten (post engagement totals get refreshed, but historical engagement events on the old post stay attached to the same id).
- **Recency window mismatch.** Engagement silence is computed relative to the same `recent_days` window. If you run with `--recent-days 7`, contacts who engaged 10 days ago show up as silent.

## Roadmap

- **Auto-extract topics.** A small classifier or LLM call to suggest topics on add, so the operator doesn't have to tag manually.
- **Draft posts in voice.** Given a recommendation, generate a draft post the operator can edit + publish.
- **Engagement-driven last_touch update.** If a contact engaged this week, treat as a dormancy refresh. Requires operator opt-in — engagement isn't the same kind of touch as a meeting or a text.
- **Compare engagement against impressions.** Engagement-rate-per-impression is a better signal than raw engagement count. Requires LinkedIn analytics-page ingestion.

## Voice

Outbound is interpretive and direct. *"Bob Gibson hasn't engaged in 42 days — post about Toast roles."* Not *"you may want to consider re-engaging your dormant connections."* The system has the data; the recommendation can be specific.
