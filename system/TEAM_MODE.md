# Team mode — design

V1 enterprise feature. Not implemented yet. This document describes how the existing single-tenant primitives compose into the team feature so engineering doesn't redesign from scratch when V1 commercialization starts.

**Status: DESIGN ONLY** as of 2026-05-16. The architectural pattern is settled; the multi-tenancy layer underneath is not built.

---

## The thesis in one sentence

Team mode is the same compute we run against one operator's baseline, run against the *union* of multiple operators' baselines, with privacy controls that release contact identity only when the contact's owner consents.

That is, every team query — *"who has a path to Brand X"*, *"where is our combined network strong"*, *"what gaps do we share"* — is just `find_intro_paths` or `network_analysis` or `network_gap` fanned out across a roster of tenant baselines and aggregated.

## The motivating scenario

Todd is selling into a target franchisee. He doesn't know anyone there. His teammate Sally on the BridgePoint sales team has a personal relationship with the franchisee's VP of Operations from a previous job. Sally is in baseline; the franchisee VP is in Sally's baseline but not Todd's.

**Without team mode.** Todd asks Slack, "Hey team, anyone know someone at Brand X?" Sally either remembers and responds, or doesn't and the path is invisible. The relationship knowledge is locked in individual brains.

**With team mode.** Todd asks RB: *"find me an intro to anyone at Brand X."* The team query fans out across the roster:

- Todd's baseline → no insiders at Brand X.
- Sally's baseline → matches the VP at Brand X. Sally's DRR score for that contact is high.

RB returns: *"Sally has a strong contact at Brand X. The contact's identity isn't shown until Sally consents to share. Want me to send Sally an intro request?"*

Todd: *"Yes."* RB drops a notification into Sally's session: *"Todd is trying to reach Brand X. You have a relevant contact there. Approve sharing?"* Sally approves; the contact's name surfaces; Sally offers a warm intro.

That whole flow runs on `find_intro_paths` aggregated across two baselines plus a consent gate. The same primitives already do single-user intros.

---

## Data model

### Single team manifest

`system/team/manifest.yaml` — the team identity, roster, roles, defaults.

```yaml
version: 1
team_id: bridgepoint-sales
team_name: BridgePoint Ops Sales
created: 2026-05-16
members:
  - id: todd
    name: Todd Vahlsing
    email: todd@bridgepointops.com
    role: founder
    baseline_path: tenants/todd/baseline_index.json
    visibility_default: tier_gated
  - id: sally
    name: Sally Lopez
    email: sally@bridgepointops.com
    role: sales
    baseline_path: tenants/sally/baseline_index.json
    visibility_default: tier_gated

team_settings:
  intro_request_ttl_hours: 72
  default_visibility: tier_gated
  reveal_on_consent: true
  team_recommendations_enabled: true
```

### Per-tenant baseline

Each team member has their own `tenants/<member_id>/baseline_index.json`. Same shape as today's single-tenant baseline. No cross-tenant contamination.

### Sharing/visibility model

Three visibility tiers per contact (operator can override per-contact):

| Visibility | Behavior in team queries |
|---|---|
| `private` | Contact is never surfaced in team queries, even as a count. Owner can still see them in their own brief. |
| `tier_gated` | Team queries see a tier-anonymized hit ("Sally has an inner-tier RC at Brand X"). Identity revealed only on consent. |
| `team_visible` | Identity visible to teammates without consent (e.g., common-knowledge contacts in the industry). |

Default is `tier_gated` — privacy-respectful while still surfacing the signal.

---

## Team queries

Each team query reuses an existing single-user primitive run across the roster.

| Single-user primitive | Team-mode counterpart |
|---|---|
| `find_intro_paths(target)` | `team_find_intro(target)` — fan-out, aggregate, surface as anonymized hits unless `team_visible`. |
| `network_gap.cluster_inner_anchor_score()` | `team_network_gap()` — combine cluster member counts across tenants; recompute anchor presence on the pooled set. Surfaces gaps the *team* has, not just one person. |
| `network_analysis()` | `team_network_analysis()` — strengths/weaknesses/bridges across the combined graph. Identifies which member is the anchor for each strength cluster. |
| `drr_score(entry)` | Unchanged (per-tenant). Cross-tenant DRR is meaningless — it's a relationship property. |
| `daily_brief.build_report()` | Per-member, but with an optional `team_signal` section: "your teammates touched the following active-thread contacts this week." |

## Consent flow

1. Team member A runs a team query that hits a contact owned by member B.
2. Result returns to A as: `"member_B has an inner-tier RC at the target — identity withheld."`
3. A clicks "request intro." RB writes a consent request into B's session queue.
4. B's next session shows: `"[A] is trying to reach [target_company]. You have a relevant contact. Approve sharing?"`
5. On approval: identity revealed to A, intro context shared, loop created in both their loop ledgers.
6. On decline or TTL expiry (72h default): request closes silently. A is told the request was declined or timed out, not the reason.

The consent step is the privacy hinge. Without it, the product becomes a contact-aggregation tool that violates the trust your contacts placed in you when they gave you their info. With it, the product is a way to ask your team for help that respects each member's relationships.

---

## What changes in the existing scripts

Surprisingly little. The team layer is mostly additive.

| Existing component | Change |
|---|---|
| `rb_core.py` loaders | Take an optional `baseline_path` argument so they can read non-default baselines. Today they're hardcoded to `system/baseline_index.json`. |
| `find_intro_paths()` | Add an optional `member_id` tag passed through to results so the team aggregator can attribute hits. |
| `network_gap`, `network_analysis` | Same — `baseline_path` parameter. |
| All write paths (`mutations.py`) | Tenant scoped. Write to the calling member's baseline only. |
| MCP server | Add `rb.team_find_intro`, `rb.team_network_gap`, `rb.team_network_analysis`. Authentication identifies the caller's member id. |
| HTTP API | Add `/team/intro`, `/team/network_gap`, `/team/network_analysis`. Same auth + tenant routing. |
| Storage layer | This is the V1 engineering work — multi-tenant storage with encrypted at-rest baselines, RBAC, audit log. |

---

## What's net-new

- `system/team/manifest.yaml` schema + loader
- Cross-tenant aggregator functions in a new `team_core.py`
- Consent request flow (write to a shared "intro requests" queue + per-member inbox)
- Privacy enforcement at every team query result-construction step
- Audit log of who-asked-for-what (compliance requirement for enterprise)
- Per-contact visibility override UI (the operator decides)
- Per-team dashboard surface (separate from individual daily briefs)

---

## V1 sequencing

Roughly mapped to the V1 launch plan's milestones:

| Milestone | Team-mode deliverable |
|---|---|
| M0 — Kickoff | This design doc finalized. Multi-tenant storage spike. |
| M1 — Pipeline alpha | Single-tenant scripts gain `baseline_path` argument. |
| M2 — DRR + briefing alpha | `team_find_intro` running against synthetic two-member team. |
| M3 — Closed beta | Team-of-3 beta with a real sales team using the consent flow end-to-end. |
| M4 — Beta exit / GA gate | Privacy + audit logging hardened. Team dashboard shipped. |
| M5 — V1 GA | Team mode is the primary commercial offering. |

The personal product (`/V0 single-tenant) remains the founder/individual operator path. Most users start there; team mode is a paid upgrade.

---

## Pricing implications

Team mode is the pricing wedge per V1 launch plan §13. Personal tier covers the V0 functionality. Team tier adds:

- Multi-member manifest
- Cross-tenant queries
- Consent flow
- Team dashboard
- Audit log
- Admin role for team config

Per-seat pricing applies. The team's combined-network analysis is the lever that justifies per-seat pricing — every additional teammate expands the searchable universe for every other teammate, so adding seats has compounding value.

---

## Risks

1. **Privacy breach is catastrophic.** A bug that leaks a private contact destroys trust at the team level *and* with the contact themselves. Privacy gates need test coverage at every aggregator entry point.
2. **Consent fatigue.** If every team query produces a barrage of requests, teammates stop approving. Default visibility tiers + smart batching matter.
3. **Disagreement on who "owns" a shared contact.** Two teammates both have the same contact in their baseline. RB needs a rule: each owner has their own DRR + relationship history; queries surface the strongest path; consent is needed from whichever owner actually has the relevant context.
4. **Departing employees.** When Sally leaves the team, her baseline goes with her. Anything she shared during her tenure is logged but her ongoing graph isn't accessible to the team. Important for both ethics and contract structure.

---

## Decision points still open

- **Does the team see anonymized hits proactively, or only on query?** Proactive ("Sally just touched a contact in your active thread") is more chief-of-staff-like but more privacy-sensitive.
- **How are cross-tenant active threads handled?** If Todd and Sally both have a Genius/Global Payments active thread, should the system surface that? Probably yes with a "your teammate is also working this" flag.
- **What's the unit of billing?** Per-seat is the obvious answer but a "graph size" cap may also be needed.

---

*Document created 2026-05-16 during the V0 build-out, as the architectural contract for the eventual V1 enterprise feature. Should be revisited at V1 kickoff (M0) and locked at M2.*
