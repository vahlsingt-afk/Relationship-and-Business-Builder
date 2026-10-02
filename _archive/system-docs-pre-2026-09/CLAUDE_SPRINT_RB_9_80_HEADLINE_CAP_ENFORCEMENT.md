# RB 9.80: Headline "5-10 Items, New Only" Enforcement — RB-DEFECT-044 (fourth slice)

**Status:** Implemented (2026-06-14)
**Source:** RB-DEFECT-044's two-document briefing architecture. RB 9.77
implemented "Upcoming Preparation Requirements" + headline "no material
developments" fallbacks; RB 9.78 implemented "Risks"; RB 9.79 implemented
"This Week"; this sprint implements Document 1's headline count enforcement.

## Scope

RB-DEFECT-044 specifies that Document 1 (Intelligence Brief)'s headline
sections — World & National, Restaurant Industry, Restaurant Technology —
should "report 5-10 significant developments... new developments only... no
filler content." Prior to this sprint, `_compute_world_headlines()`,
`_compute_restaurant_industry_headlines()`, and
`_compute_restaurant_tech_headlines()` returned the entirety of
`web_scan`'s `world_national`/`restaurant_industry`/`restaurant_technology`
lists with no cap — on days with active scanning, sections could carry 100+
items, violating the "newspaper front page" framing the rest of the
architecture establishes.

"New only" is already enforced by the existing intelligence-lifecycle pass
(RB-DEFECT-018/022): previously-reported items are suppressed before these
sections are finalized. This sprint adds the missing piece — the upper bound.

This Month, Horizon Watch (30-90 days), and Learned Patterns remain deferred
per the RB 9.78 audit (need new signal sources or historical-tracking infra).

## What was implemented

`system/scripts/daily_brief.py`:

- New capping pass, inserted immediately before the RB 9.77 "no material
  developments" fallback loop (so it runs after intelligence-lifecycle
  filtering has already removed previously-reported items, and the fallback
  still fires correctly if a section is empty after capping — which it never
  is unless it was already empty pre-cap).
- For each of `world_national_headlines`, `restaurant_industry_headlines`,
  `restaurant_technology_headlines`: if the section has more than 10 items,
  sort by `extras.signal_weight` (descending — higher weight = more
  newsworthy per `web_scanner.signal_weight()`), with `extras.pub_date` as a
  tiebreaker (newer first), and truncate to the top 10.
- No change needed to the rendering rule — it already instructed "list 5-10
  items, newest/most material first" (line ~14870); the cap now makes that
  instruction achievable on days with heavy scan volume.

## Deferred

- **This Month** and **Horizon Watch (30-90 days)** as distinct Daily Brief
  sections — larger scope, likely need new signal sources.
- **Learned Patterns** — needs historical pattern-tracking infrastructure.

## Tests

Full suite: `python3 -m pytest system/tests/ -q` — expected 2380 passed. No
new tests added (pure post-processing of existing sections, exercised
implicitly by existing canonical-brief tests, consistent with RB 9.77-9.79).
