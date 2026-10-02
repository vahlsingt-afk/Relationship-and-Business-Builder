# RB-DEFECT-035 — Morning Headlines Web-Scan Wiring Gap

**Filed:** 2026-06-08  
**Severity:** High — the `morning_headlines` section (one of the four
front-matter blocks added in DEFECT-021/031) was always empty, even on
mornings when 160 articles were successfully fetched  
**Status:** Resolved — 2026-06-08

---

## Symptom

`canonical_brief.sections.morning_headlines` contained 0 items on every brief
run despite the intelligence_assessment cache containing 160 classified
articles (70 world/national, 78 restaurant industry, 12 restaurant technology).
The section rendered as "No fresh headlines this cycle."

## Root Cause

Build-ordering bug in `daily_brief.py`:

1. `_build_morning_headlines(market_signals_report)` is called at line 625
   (early in `build_report`) — it reads from `market_signals_report.get("top")`.
2. `market_signals` was stale (last refreshed 2026-06-01 with 0 sources OK).
3. The web scanner (`_run_web_scan`) isn't loaded until line 742
   (`report["web_scan"]`), *after* `morning_headlines` was already computed
   and stored.
4. `_morning_headlines_section_items(report)` reads from
   `report["morning_headlines"]` — which was set to `[]` at step 2 — and never
   looked at `report["web_scan"]` as a fallback.

Both paths existed. Neither connected. Classic wiring-gap pattern
(same failure class as DEFECT-031/032/034).

## Fix Applied

**`daily_brief.py`:**
- Added `_morning_headlines_from_web_scan(report, limit=7)` — reads directly
  from `report["web_scan"]` buckets in priority order (restaurant_technology
  first, then restaurant_industry, then world_national).
- Extended `_morning_headlines_section_items` with a fallback path: when
  `report["morning_headlines"]` is empty (market_signals stale), calls
  `_morning_headlines_from_web_scan` and re-shapes the already-canonical
  web_scan items into the `{headline, source, date, why_it_matters, url,
  confidence}` shape used in the rendering loop.
- The section now has real headlines whenever *either* source has data —
  market_signals OR web_scan.

## Verification

Tested against live intelligence_assessment cache (160 articles). With
`morning_headlines: []` (simulating stale market_signals), the section now
renders 7 headlines:
- 7× Restaurant Technology items (Restaurant Dive + Restaurant Technology News)
- All with correct titles and summaries
- No double-category prefix
- Fallback gracefully produces "No fresh headlines" only when both sources empty

## Related Finding — Newsletter Extraction (not a code bug)

Investigation of `passive_email_intelligence` (0 signals in cache):
- Root cause: no restaurant trade newsletters (NRN, Restaurant Dive, QSR
  Magazine, etc.) are arriving in the two monitored email inboxes. The
  pipeline is correct; the subscriptions don't exist.
- **One real fix**: added `paymentsdive` / `divenewsletter.com` to
  `INDUSTRY_SOURCE_HINTS` — Payments Dive IS in the personal inbox and is
  relevant to restaurant/fintech signals. Now extracting 3 headlines
  (scores 40–55). Cache regenerated.
- **Subscription gap**: to get restaurant trade newsletters in email, subscribe
  to Restaurant Dive, NRN, Modern Restaurant Management, etc. using a Gmail
  address RB already monitors (vahlsingt@gmail.com or todd@bridgepointops.com).
  Until then, the web scanner (RSS) is the correct channel for those sources.

## Files Changed

- `system/scripts/daily_brief.py` — fallback in `_morning_headlines_section_items`
- `system/scripts/passive_email_intelligence.py` — added Payments Dive to
  `INDUSTRY_SOURCE_HINTS`
