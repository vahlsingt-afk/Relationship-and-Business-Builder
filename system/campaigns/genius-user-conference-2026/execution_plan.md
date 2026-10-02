# Global Payments Genius User Conference 2026 — Execution Plan

_Generated 2026-10-02T10:14:13+00:00_

This is a recommended sequence for you to execute manually — nothing here sends automatically.

## Week 1 — Top-ranked net-new invitations

- Send 25 top-ranked invitations (Queue A rank 1-25)
- **Messaging:** Use each contact's suggested_linkedin_outreach draft — review before sending, per RB's send_allowed=False invariant.
- **Follow-up:** If no response in 5-7 business days, one follow-up. No further follow-up without a new signal.

## Week 2 — Remaining enterprise targets + executive introductions + registration monitoring

- Send next 50 invitations (Queue A rank 26-75)
- Pursue the top 15 executive introduction(s) via known brokers this week (44 total gap companies have a broker path — see coverage_gaps.json for the rest)
- Re-run --reconcile-workbook / --reconcile-registrations against any new registration export

## Week 3 — Regional/growth brands + follow-up campaign + executive escalation

- Send remaining 50 invitations (Queue A rank 76-125)
- Follow up with 3 invited-not-registered contact(s) (Queue B)
- Escalate 3 Tier 0 Worldpay customer contact(s) directly

## Week 4 — Final invitation wave + registration recovery

- Send final 21 invitations (remainder of Queue A)
- Registration recovery push for anyone still in Queue B (3 as of this run)

## Tracking

- Invitations sent (to date): 3
- Registrations: 0
- Attended: 0  |  Declined: 0
- Acceptances: not computed — RB tracks invited/registered/declined status; a distinct 'accepted the invite but hasn't registered yet' signal isn't captured by any ingested source.
- Responses: not computed — Requires reply/interaction tracking cross-referenced per contact — interaction_capture.py records that an interaction occurred, not its content or direction.
- Conversion rate by relationship strength:
  - Light: 0.0%
  - Moderate: 0.0%
