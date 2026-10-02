# Claude Handoff — LinkedIn Ended Roles Must Not Persist as Current Employment

**Date:** 2026-08-06  
**Requested by:** Todd Vahlsing  
**Priority:** High — relationship graph and company-leadership accuracy  
**Example:** Richard Heyman / Scooter's Coffee

## Product problem

RB can retain a LinkedIn role as a person's `current_company` and `current_role` even when the LinkedIn profile shows that the role ended and no subsequent role is listed.

Concrete example:

- Richard Heyman's Scooter's Coffee role ended in March 2025.
- His LinkedIn profile lists no newer role.
- RB still treated him as a current Scooter's executive because the stored company/title persisted.
- Todd confirmed Tyler Marpes is now the person in charge of technology at Scooter's.

This creates two downstream failures:

1. People with no stated current role are not visible as potential relationship/career-support opportunities.
2. Company profiles, leadership maps, campaigns, warm-path recommendations, and account coverage include people who no longer work there.

## Required canonical behavior

When date-bearing LinkedIn/profile evidence shows that a person's most recent listed role has ended and no other role is marked `Present`, `Current`, or `Now`:

- Set `current_company` to `null`.
- Set `current_role` to `null`.
- Set `employment_status` to `no_stated_current_role`.
- Preserve:
  - `last_known_company`
  - `last_known_role`
  - `last_known_role_dates` or normalized end date
  - the original experience list and source provenance
- Add a durable marker such as `linkedin_no_stated_current_role`.
- Exclude the person from active company leadership, insider, account-map, campaign, and warm-introduction calculations.
- Make the person queryable as a career-support candidate, but do not assume they are unemployed. The precise wording must remain “no stated current role.”

When a later profile shows a role marked Present/Current/Now:

- Populate the new `current_company` and `current_role`.
- Change `employment_status` to `stated_current_role`.
- Remove the no-current-role marker.
- Preserve prior employment history.

If the source has no employment dates, do not infer that the role ended. Mark date confidence as unavailable and retain the best-known current value unless stronger evidence exists.

## Important source limitation

LinkedIn's standard `Connections.csv` export includes `Company` and `Position`, but it does **not** include employment start/end dates for connections. The July 29 complete archive was inspected and confirms this. `Positions.csv` contains Todd's own employment history, not the employment history of every connection.

Therefore this cannot be solved reliably from the official LinkedIn connections download alone. Date-aware clearing requires one of:

- the existing operator-run LinkedIn browser-session profile capture;
- another permitted enriched profile source containing experience dates;
- explicit operator correction;
- a future source that provides dated employment history.

Do not scrape or fabricate dates. Do not treat disappearance from `Connections.csv` as employment termination; that only indicates connection/export state.

### Open-to-Work visibility limitation

The downloaded LinkedIn network data does not provide a reliable Open-to-Work field for connections.

- `Connections.csv` contains only name, profile URL, permitted email, company, position, and connection date.
- `Jobs/Job Seeker Preferences.csv` describes Todd's own LinkedIn job-seeker settings, not the status of people in his network.
- A small number of connections may put phrases such as “open to work,” “seeking,” or “open to advisory roles” in their visible position/headline. Treat that only as self-authored text evidence, not as LinkedIn's Open-to-Work setting.

Safe future behavior:

- Capture an explicit visible Open-to-Work badge/frame or unambiguous self-authored headline only through a permitted operator-opened profile capture.
- Store provenance, capture date, exact signal type, and freshness because Open-to-Work status can change quickly.
- Distinguish `explicit_open_to_work`, `self_authored_opportunity_language`, and `no_stated_current_role`; never collapse them into one “job seeker” label.
- Do not infer Open to Work merely because current company is blank or the last role ended.
- Rank supportive outreach using relationship strength and recency, but phrase recommendations humanely: check in, offer help, or ask what they are exploring—not “I saw you are unemployed.”

## Work already implemented

### Date-aware profile resolution

Updated `system/scripts/linkedin_session_reader.py`:

- Added `_employment_state(experience)`.
- Searches all experience entries for `Present`, `Current`, or `Now` rather than assuming the first entry is current.
- Recognizes explicit ended date ranges.
- Clears active company/title when all dated roles have ended.
- Preserves last-known employment fields.
- Adds/removes `linkedin_no_stated_current_role`.
- Returns `career_support_candidate` for downstream use.

### Regression coverage

Added `system/tests/test_linkedin_session_reader_employment_dates.py` covering:

- ended role with no successor;
- current role not listed first;
- missing-date fallback;
- persisted clearing of stale company/title;
- last-known employment preservation;
- career-support tagging.

Relevant test result: 12 tests passed across the new tests, LinkedIn delta ingestion tests, and full-archive watcher coverage. Python compilation passed using a temporary bytecode cache.

### Canonical Richard/Tyler correction

Updated `system/baseline_index.json`:

- Richard Heyman:
  - `current_company: null`
  - `current_role: null`
  - `employment_status: no_stated_current_role`
  - last known Scooter's role ending March 2025
- Tyler Marpes remains at Scooter's and is marked by Todd as the current technology decision lead.

Updated `system/account_intelligence/2026-08-06-scooters-coffee-par-rfp-signal.md` and rebuilt the Genius user-conference campaign so Richard no longer appears as an active Scooter's alternate contact.

## Remaining work for Claude

1. **Make employment state a canonical shared helper.**
   - Move or expose date parsing so all profile/enrichment adapters use the same rules, not only `linkedin_session_reader.py`.

2. **Add provenance and confidence fields.**
   - Recommended fields:
     - `employment_status_source`
     - `employment_status_observed_at`
     - `employment_end_date`
     - `employment_date_confidence`
   - Operator-confirmed corrections must outrank stale connection-export company/title values.

3. **Protect corrections during future `Connections.csv` ingests.**
   - `linkedin_ingest.py` currently treats LinkedIn company/title as movement evidence.
   - Ensure a date-backed or operator-confirmed `no_stated_current_role` state is not overwritten by the same stale company/title from a later Connections export.
   - Add a regression test using Richard's pattern: canonical ended-role state plus stale Connections row must retain blank current company/title and register a conflict, not restore Scooter's.

4. **Update all active-company consumers.**
   Audit and test at minimum:

   - company leadership/insider maps;
   - `rb_core.py` company matching and warm-path logic;
   - campaign roster/account-first recommendations;
   - meeting prep and relationship recommendations;
   - ecosystem/entity account coverage;
   - query engine answers about “who do I know at company X?”

   All should require nonblank `current_company` and should ignore `employment_status=no_stated_current_role` for current-company membership.

5. **Create a no-stated-current-role network view.**
   - Query/report people tagged `linkedin_no_stated_current_role`.
   - Rank by relationship strength, recency, strategic relevance, and evidence freshness.
   - Offer supportive outreach only when appropriate; never label the person unemployed or job-seeking without evidence.

6. **Add reconciliation output after LinkedIn refresh.**
   The refresh report should distinguish:

   - new current role;
   - ended role with no stated successor;
   - stale export conflict suppressed;
   - employment dates unavailable;
   - needs operator review.

   Also report explicit, fresh Open-to-Work/profile-opportunity signals separately when a permitted date-bearing profile capture supplies them. Do not claim network-wide Open-to-Work coverage from the LinkedIn archive.

7. **Rebuild derived artifacts automatically after employment corrections.**
   - Current campaign rebuild was performed manually.
   - Ensure canonical employment changes invalidate/rebuild company coverage and campaign artifacts that rely on `current_company`.

## Acceptance tests

1. Richard profile evidence: Scooter's role ends March 2025, no newer role.
   - Current company/title remain blank.
   - Last-known employment is retained.
   - Richard does not appear in “who do I know at Scooter's?”
   - Richard appears in the no-stated-current-role support view.

2. A later stale `Connections.csv` row still says Scooter's / EVP.
   - It does not restore active employment.
   - It produces a visible suppressed-conflict record.

3. A later dated profile shows Richard at a new company with `Present`.
   - New current company/title populate.
   - No-current-role marker clears.
   - Scooter's remains in employment history only.

4. Tyler Marpes remains Scooter's current technology lead in company and relationship queries.

5. Missing-date sources do not clear employment without stronger evidence.

## Product principle

Current company membership must be evidence-backed and temporally valid. Historical employment is valuable relationship context, but it must not contaminate current leadership maps. “No stated current role” is both a data-quality state and a potentially meaningful human-support signal—not a conclusion about unemployment.
