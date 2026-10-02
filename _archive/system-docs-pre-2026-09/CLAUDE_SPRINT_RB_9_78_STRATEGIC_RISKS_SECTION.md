# RB 9.78: Standalone "Risks" Section — RB-DEFECT-044 (second slice)

**Status:** Implemented (2026-06-14)
**Source:** RB-DEFECT-044's two-document briefing architecture (Document 2 =
Daily Brief, redefined canonical structure). RB 9.77 implemented "Upcoming
Preparation Requirements"; this sprint implements "Risks".

## Audit summary (against RB-DEFECT-044's Document 2 structure)

An agent audit mapped all 12 canonical Daily Brief sections against the current
implementation:

| Item | Status |
| --- | --- |
| Executive Summary | Existing (`executive_summary`) |
| Today | Existing, distributed across `five_things_today`/`cos_today`/`critical_event_today`/`day_ahead`/etc. |
| This Week | Gap — no dedicated section (deferred) |
| This Month | Gap — no dedicated section (deferred) |
| Horizon Watch (30-90 days) | Gap — `upcoming_preparation_requirements` covers 30-day *prep*, not 30-90 day *developments to watch* (deferred — larger scope, likely needs new signal sources) |
| Opportunities | Existing (`opportunity_board`, `opportunity_signals`, `job_intelligence`) |
| **Risks** | **Critical gap** — only `weekly_plan.json`'s `risks` list surfaced, buried inside plan-derived items; no standalone section |
| Relationships Requiring Attention | Existing (`relationship_momentum_status`) |
| Decisions Approaching | Existing (`decision_layer`) |
| Signals and Patterns | Existing, distributed (`emerging_themes`, `connect_the_dots`, `contrarian_view`) |
| Upcoming Preparation Requirements | Done (RB 9.77) |
| Learned Patterns | Deferred — needs new historical-tracking infrastructure |

"Risks" was the clearest, smallest, highest-value net-new gap: a standalone
section synthesizing what's newly at risk, distinct from "what should I decide"
(`decision_layer`) and "what opportunities exist" (`opportunity_board`).

## What was implemented

`system/scripts/daily_brief.py`:

1. **New section `strategic_risks`** (Document 2/Daily Brief), added to
   `brief_display_order` Bucket 5 ("Today"), immediately after `decision_layer`
   (renders decisions first, then what's at risk if they/relationships aren't
   tended to).
2. **New `_compute_strategic_risks(report, sections)`**, synthesizing four risk
   categories into one section:
   - **Weekly-plan risks**: `weekly_plan.json`'s `risks` list (up to 3),
     same framing previously used inside the weekly-plan synthesis item.
   - **Competitive-vulnerability escalations**: items from
     `competitive_vulnerability_watchlist` with disposition `act`/`act_today`/
     `ask_todd`, reframed as "Competitive risk: ...".
   - **Relationship deterioration**: FROZEN/COLD contacts (from
     `relationship_momentum_status`) with at least one open loop — rolled up
     into a single item naming up to 5 contacts, `disposition=act_today`.
   - **Scheduling/operational-debt pressure**: "Scheduling overload risk" when
     today's calendar has ≥5 events; "Operational debt risk" when ≥3 loops are
     overdue.
   - If nothing qualifies, emits a single "No new risks detected" item
     (`disposition=ignore`) — same "no material change" trust pattern as
     RB 9.77's headline fallbacks.
3. **New rendering rule**: renders `strategic_risks` as "⚠️ Risks" after
   `decision_layer`; green-board confirmation for the empty case; otherwise
   `• [title] — [why_it_matters] → [recommended_action]` with `[ACTION TODAY]`/
   `[MONITOR]` prefixes by disposition.

## Deferred

- **This Week / This Month / Horizon Watch (30-90 days)** as distinct
  Daily Brief sections — larger scope, Horizon Watch in particular likely needs
  new signal sources (earnings calendars, industry event calendars).
- **Learned Patterns** — needs historical pattern-tracking infrastructure.
- Headline "5-10 items, new only" enforcement (Document 1) — not addressed this
  sprint.

## Tests

Full suite: `python3 -m pytest system/tests/ -q` — expected 2380 passed. No new
tests added (pure aggregation over existing sections, exercised implicitly by
existing canonical-brief tests, consistent with RB 9.75-9.77).
