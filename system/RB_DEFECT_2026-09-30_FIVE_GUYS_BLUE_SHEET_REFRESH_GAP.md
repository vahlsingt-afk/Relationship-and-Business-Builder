# RB Defect 2026 09 30 Five Guys Blue Sheet Refresh Gap

## Summary

Five Guys is classified as `active_engagement` and has current account research and Worldpay portfolio intelligence, but its Blue Sheet remains an unactivated shell. The Friday end of week routine and the daily intelligence cascade both use `workbook_path is not None` as their definition of an activated Blue Sheet. Five Guys has `workbook_path: null`, so both mechanisms skip it by design.

The result is a split state:

- Account Background Brief: current and regenerated from September intelligence.
- Worldpay Master Account Plan: current through September 30.
- Blue Sheet account dossier returned by `getAccountStatus`: stale August shell.
- Weekly Blue Sheet freshness review: Five Guys omitted entirely.

## User expectation

Todd stated on September 30, 2026:

> the 5 guys blue sheet should be getting refreshed during our weekly end of week cycle at a very minimum. The fact that it hasn't updated shows that we have a gap somewhere.

## Evidence

### Registry state

`customers_prospects/_portfolio/customers_prospects_registry.json` records Five Guys as:

- `engagement_tier: active_engagement`
- `status: missing_blue_sheet`
- `workbook_path: null`
- `last_review_date: null`
- `latest_background_brief_date: 2026-09-25`

The mirrored `blue_sheets/_portfolio/blue_sheet_registry.json` has the same null workbook and missing Blue Sheet status.

### Blue Sheet dossier

`customers_prospects/accounts/five-guys/account.json` was last materially written in August and still says:

- shell only;
- no dedicated source material found;
- next formal review on the first dedicated brief, meeting or document.

Those statements are now false. Dedicated Five Guys research, direct customer evidence and a current Worldpay portfolio entry exist.

### Current downstream artifacts

- `system/account_intelligence/2026-09-04-five-guys-canonical-research.md` contains the direct customer response, outreach hold, buying influence and technology context.
- `customers_prospects/accounts/five-guys/briefs/current/Background_Brief.md` contains the current September account posture and is regenerated daily.
- `master_account_plans/vendors/worldpay/rm_portfolios.json` contains the current P1 account record and September 30 refresh.
- The live `getAccountStatus` API still returns the stale shell because it reads `account.json`, not the regenerated Background Brief or Master Account Plan.

### Friday routine behavior

`system/scripts/friday_eow_routine.py::check_freshness()` explicitly does:

```python
if entry.get("workbook_path") is None:
    continue
```

Its own regression test asserts that an unactivated account is skipped. The September 25 Friday result therefore lists only the three workbook backed accounts and does not mention Five Guys.

The routine is scheduled and did run. This is not a scheduler failure; it is a scope and state classification failure.

### Intelligence cascade behavior

`blue_sheets/_engine/common.py::is_activated()` returns true only when `workbook_path` is non null. `system/scripts/intelligence_cascade.py` uses that gate before Blue Sheet synchronization. Five Guys therefore cannot receive Blue Sheet mutations or even a Blue Sheet review queue item through this path.

The cascade is also intentionally narrow: it only auto synchronizes an unambiguous existing technology stack row from a newly touched ecosystem relationship. It does not reconcile a current Account Background Brief or Master Account Plan into a stale Blue Sheet dossier.

### Background Brief behavior

`refresh_persisted_briefs.py` regenerates the Five Guys Background Brief every day. Its renderer reads the dedicated `system/account_intelligence` documents and ecosystem relationships directly. That explains why the Background Brief is current while `account.json` is not. The generation path is read only with respect to the structured Blue Sheet dossier; it does not write the extracted facts back into `account.json`.

## Root cause

The primary root cause is an invalid state combination that the system permits but does not reconcile:

> Five Guys is an active engagement with an account dossier and current research, but has no activated Blue Sheet workbook.

Three design gaps compound it:

1. The Friday routine treats “no workbook” as “not a Blue Sheet” and silently excludes the record instead of flagging an active engagement that is missing its Blue Sheet.
2. The daily cascade uses the same workbook gate, so current intelligence cannot reach the structured account dossier.
3. The daily Background Brief renderer can consume current narrative research without promoting reviewed facts into the structured dossier, creating two materially different answers for the same account.

## Impact

- `getAccountStatus` gives an obsolete account view.
- The end of week report falsely appears complete while omitting an active strategic account.
- Direct customer contact restrictions and relationship ownership are not visible in the structured Blue Sheet response.
- New intelligence can refresh the Background Brief and Master Account Plan without creating a mutation, review item or explicit exception for the Blue Sheet.
- The same failure can affect any account with `engagement_tier: active_engagement` and `workbook_path: null`.

### Confirmed blast radius

The production registry currently contains two accounts in this invalid combination:

- `acct-five-guys`
- `acct-del-taco`

Both are active engagements with current Background Brief dates, null workbook paths and null Blue Sheet review dates. This is therefore a class defect, not a Five Guys-only data exception.

## Recommended repair

### Immediate invariant

Add a registry invariant and acceptance check:

```text
engagement_tier == active_engagement AND workbook_path is null
```

must be reported as `active_engagement_missing_blue_sheet`, never silently skipped.

### Friday routine

- Include active engagement records without workbooks in the freshness result.
- Mark them stale or missing with the explicit reason `active engagement has no activated Blue Sheet`.
- Include them in the human readable report and execution receipt.
- Add regression coverage using the Five Guys state shape.

### Daily cascade

- When current intelligence touches an active engagement without a workbook, create a durable coverage or review item rather than treating it as an ordinary unactivated brand.
- Distinguish `pre_engagement_no_blue_sheet` from `active_engagement_missing_blue_sheet`.
- Verify the cascade receipt reports this state.

### Five Guys repair

- Use Todd's explicit direction to activate and populate the existing Five Guys research shell through the governed Blue Sheet activation path.
- Populate it only from current persisted evidence: the September 4 canonical research, current Background Brief, Worldpay Master Account Plan and direct customer evidence.
- Preserve the current outreach hold, Avery and Melissa routing, lack of confirmed RFP and unapproved status of the transaction metered concept.
- Set a real review date and render the workbook.

### Reconciliation control

Add a weekly cross artifact consistency check for each active engagement:

- latest Background Brief date;
- latest Master Account Plan evidence date when linked;
- Blue Sheet last review date;
- workbook activation state;
- material posture conflicts between those artifacts.

The check should flag differences for review rather than synthesizing unsupported field changes.

## Severity

High. The defect causes RB's primary active account status operation to return stale information while fresher canonical artifacts exist, and the weekly control designed to catch staleness omits the account entirely.

## Status

Diagnosed on September 30, 2026. No Blue Sheet mutation or activation was performed during this investigation.

The existing Friday-routine and intelligence-cascade tests pass, including a test that explicitly asserts workbook-less accounts are skipped. The current behavior is implemented and tested as designed; the design no longer matches Todd's required active-account control.
