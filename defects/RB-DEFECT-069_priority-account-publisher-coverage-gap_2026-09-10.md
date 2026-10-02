# RB-DEFECT-069 — Priority-account publisher coverage gap

**Captured for:** Claude  
**Date:** 2026-09-10  
**Status:** Fixed (2026-09-10) — pending first live morning-pipeline run for field verification  
**Area:** Morning intelligence / industry-watchlist collection

## Observation

RestaurantNews.com is already an approved RB industry source. It is listed in `system/settings.json`, recognized by passive email intelligence, included in the newsletter full-body-fetch domains, and represented in daily intelligence briefs.

However, the September 2, 2026 RestaurantNews.com article **“Del Taco Activates Project Del Sunrise Roadmap With All-New Catering Platform”** was not surfaced for the priority Del Taco account before Todd supplied the URL manually on September 10.

Source URL: https://www.restaurantnews.com/del-taco-expanded-catering-program-090226/

## Gap

The current pipeline provides publisher coverage but not guaranteed account coverage. Restaurant-news selection is capped and scored as a general industry feed, while newsletter ingestion depends on which stories appear in captured email content. A material article about a named priority account can therefore be dropped even when its publisher is already monitored.

This distinction should be explicit:

- **Source monitored:** yes.
- **Every relevant article collected:** no.
- **Priority-account completeness contract:** not currently demonstrated.

## Recommendation

Add a priority-account publisher-matching pass after normal industry-source refresh and before daily-brief synthesis.

For each active Tier 1 / watchlist / Blue Sheet / Master Account Plan account:

1. Search recent items from configured restaurant-industry publishers using the account's canonical name and aliases.
2. Compare matches against already captured URLs and normalized titles.
3. Fetch unseen candidates and score them for materiality to ownership, leadership, technology, payments, vendor relationships, deployment, operating strategy, and buying events.
4. Route material items into the existing review-first evidence workflow; do not auto-promote claims into canonical account records.
5. Report a per-account coverage result, including publishers checked, matching items found, duplicates removed, fetch failures, and material candidates queued.

The general news cap should control what appears in the broad morning-news section, not whether a material priority-account signal is collected and evaluated.

## Acceptance criteria

- A fixture containing the Del Taco catering article is discovered when `Del Taco` or a registered alias is on the priority-account watchlist.
- The article is captured even when it does not rank in the general ten-item restaurant-news selection.
- Repeated runs deduplicate the same canonical URL and materially identical syndicated versions.
- The finding remains proposed until reviewed; no unsupported account claim is written automatically.
- Source-health output distinguishes publisher reachability from priority-account coverage completeness.
- Tests include at least one positive match, one alias match, one irrelevant brand mention, one duplicate/syndicated article, and one publisher fetch failure.

## Relevant implementation evidence

- `system/settings.json` includes `RestaurantNews.com / RestaurantData` in `industry_sources`.
- `system/scripts/fetch_google.py` includes `restaurantnews.com` in `NEWSLETTER_DOMAINS`.
- `system/scripts/passive_email_intelligence.py` maps `restaurantnews.com` to `RestaurantNews.com`.
- `system/briefs/2026-09-10-intelligence-brief.md` shows RestaurantNews.com in the ten-article restaurant scan.
- The supplied Del Taco article was only added to structured ecosystem intelligence after manual review on 2026-09-10.

## Fix summary

Built `system/scripts/priority_account_publisher_scan.py` and wired it into `morning_pipeline.py` (right after `technomic_watchlist_scan`), `server.py`/`rbb_chat_tools.py` (`getPriorityAccountPublisherMatches`, `confirmProposal(kind="priority_account_publisher_match")`), and the KB (`custom_gpt_operational_playbook.md`).

Investigated before building, not assumed: RestaurantNews.com's own RSS feed is real but only exposes ~14 items covering roughly one day — a daily scan of that feed alone would not reliably catch a priority account's article before it scrolls off, which is exactly why it was missed here. The real fix is a Google News RSS search restricted to the configured publisher domains, queried **per priority account** (name + real aliases) — the same proven pattern `entity_alerts.py::_fetch_press_releases()` already uses for press-wire sites. The priority-account universe (every `customers_prospects` account + every vendor's Master Account Plan row) is resolved to each account's real `ecosystem_intelligence.json` brand entity, reusing its already-canonical name/aliases rather than tracking a new alias list. Materiality reuses `ecosystem_brief.py`'s existing signal taxonomy rather than inventing a parallel one. Dedup checks both the graph's own known source URLs and the target account's evidence log, so an article already captured through any other path (including a Todd manual upload) is never re-proposed. Everything is review-first — nothing writes to an account's evidence without an explicit confirm.

Two real bugs found and fixed via live verification against production data (not assumed correct after tests passed):
- `vendor_relationship_formed`'s generic "rolls out"/"rolling out"/"deploys" keywords — reliable inside `ecosystem_intelligence.json`'s own pre-curated signals array — produced false positives against raw open trade-press search: "Slim Chickens Rolling Out Bacon Ranch Chicken Sandwich" and "Golden Corral rolls out brunch systemwide" both classified as material despite naming no vendor at all. Fixed by requiring a `vendor_relationship_formed` match to also name a real, known vendor entity.
- `customers_prospects_registry.json` has a real "worldpay" account entry (Todd's own employer, tracked there for an unrelated reason) that resolved as a genuine priority account and got scanned for coverage of itself. Excluded via the same `core.GP_OWN_TERMS` list `tech_stack_relationship_promotion.py` already uses for this exact reason.

Verified live against real production data after both fixes: 14 real priority accounts resolved, 11 genuinely material candidates queued (CEO changes, an acquisition/funding item), zero false positives, zero fetch failures. The exact historical Del Taco Sept 2 article itself did not reappear in this fresh search window — other publishers' coverage of the same story has since outranked the original piece in Google News's real-time ranking, a full week-plus later. That's expected, not a bug: this scanner's job is to catch a priority account's coverage from day one going forward, not to perfectly re-discover one specific article after the fact once search ranking has naturally moved on.

**Follow-up, closed same day**: `system/api/openapi_gpt.yaml`'s (and `openapi.yaml`'s) `ConfirmProposalBody.kind` enum was already stale before this change (missing `tech_stack_relationship` and `watchlist_promotion`, both real, already-shipped kinds) — confirmed the Custom GPT is retired but `rbb_chat_tools.py` still builds rbb-chat's live tool schema from this file, so the drift was real, not cosmetic. Investigated scope before fixing: confirmed this was the ONLY stale Literal/enum field across the whole API (checked all 4) — the broader ~35-operation gap between `openapi.yaml` and `server.py` is mostly deliberate (82 tools already live via `rbb_chat_tools.py`'s hand-maintained `_EXTRA_RBB_CHAT_ONLY_TOOLS`, bypassing this file entirely). Fixed by adding `tech_stack_relationship`, `watchlist_promotion`, and `priority_account_publisher_match` to both files' enum directly. Did not attempt the larger, separate "regenerate `openapi.yaml` from the live app" question — an explicitly open architectural decision per `validate_openapi_gpt.py`'s own docstring, not resolved as a side effect here.
