# Network Heuristics

Cluster-level rules that affect the intro engine and other relationship reasoning. Each entry captures something about Todd's network that holds across many specific cases — patterns the system should know without re-learning them through correction every session.

**This file is read at session start, alongside the selected user profile, `README.md`, `01_RB_TENETS.md`, and `ARCHITECTURE.md`.** Bootstrap should fail loudly if this file is missing or stale.

**Rules are operator-stated unless explicitly inferred.** When a heuristic is inferred (from patterns in baseline, IBs, calendar evidence), it is marked as such and held with lower confidence than operator-stated rules.

---

## How the intro engine uses this file

Before producing any intro proposal, consult the heuristics here. If a heuristic suppresses a candidate intro, the engine **does not** propose it. If a heuristic flags a pair as already-acquainted, the engine treats the pair as a Same-Circle case under the `community_chapter` rule (see ARCHITECTURE.md → "Same-Circle behavior depends on `circle_type`").

If a heuristic conflicts with a Same-Circle/`affinity_circle` proposal that would otherwise be valid, the **heuristic wins**. Heuristics are operator knowledge; Circle membership is a category Todd may not have actively curated for every member.

---

## Cluster-level acquaintance rules

These rules express that "if person A is in cluster X, they almost certainly know person B." The intro engine should suppress proposals of A → B in those cases.

### SCN orbit knows Donnie Boivin
**Source:** Todd 2026-05-12. **Confidence:** high.

Anyone who is an SCN (Success Champions Networking) member, attends an SCN chapter (including Hospitality Table), or has been an SCN guest, already knows Donnie Boivin. Donnie founded SCN and is the central figure. Do **not** propose Donnie as an intro target for any contact who is in the `success-champions` or `hospitality-table` Circles, tagged `success-champions`, or otherwise SCN-orbit per their notes.

Donnie is only a relevant intro target for **non-SCN contacts** who would benefit from joining the SCN orbit. (And even then, the intro is "introduce them to SCN," not "introduce them to Donnie personally" unless the value proposition is specific.)

### OTP3 cohort all know each other
**Source:** Inferred from baseline + selected user profile. **Confidence:** high.

The OTP3 leadership cohort (Bruce Sellnow, Dave Richards, John Adams, Todd) all know each other deeply — daily-cadence text contact, decades of shared history through Valley Management / McDonald's / OTP Level 3. Do not propose intros among this group; they're operating as a unit, not a network to be activated.

### Hospitality Table regulars know each other
**Source:** Inferred from chapter participation. **Confidence:** high.

Anyone tagged with the `hospitality-table` Circle (current members and past attendees: Noelle Labrie, Chason Forehand, Cristina Gia Luciano, Daran Adair, Ruth Mallon, Cheryl Powell, Mike Porto, Page Driscoll, Kristen Chimack, Hassan Choudhury, Melissa Haen, Brandon Jordan, Maggie Benson) has either currently or in the recent past been in chapter meetings together. Do **not** propose intros among `hospitality-table` members. Out-of-chapter bridges are the valuable moves for these contacts.

### Former PAR cohort knows each other (mostly)
**Source:** Inferred from BridgePoint Ops partnership conversation note. **Confidence:** medium.

The four ex-PAR named in the `former-par-employees` Circle (Dave Richards, Amy Spytko, Jeff Wayman, plus one — confirm with Todd) explored a BridgePoint Ops partnership together. They know each other. The newer addition (Jenny Kurdle) is a former boss of Todd's at PAR; whether she knows each of the four directly is unconfirmed. Don't propose intros among the original four; for Jenny → any of the four, confirm with Todd before suggesting.

---

## Already-known pairs (operator-confirmed)

Specific A↔B pairs Todd has flagged as already-acquainted. These act as targeted suppressions in addition to the cluster rules above.

| Person A | Person B | Source | Notes |
|---|---|---|---|
| Daran Adair | Jim Taylor | Todd 2026-05-12 | Both are restaurant-consulting peers; long-standing acquaintance. |
| Mike Porto | Paul McCarthy | Todd 2026-05-12 | Already known via vendor / facility-services overlap. |
| Kristen Chimack | Donnie Boivin | SCN-orbit rule | Covered by "SCN orbit knows Donnie" — listed here as a worked example. |

(Add new rows here as Todd surfaces additional already-known pairs. This is the place to drop one-off corrections instead of burying them in card notes.)

---

## Intro broker rate-limiting (Tenet 11b carry-forward)

These are stronger versions of the general rate-limit rule, applied to specific brokers in Todd's network.

| Broker | Rule | Source |
|---|---|---|
| *(none yet specified)* | | |

(Examples to add when relevant: "Jenny Kurdle has brokered 2 intros in the last 90 days — back off until next quarter." "Alison Simmons is the warmest path into restaurant-tech enterprise; protect for high-stakes targets.")

---

## Outside-SCN-but-restaurant-network rules

Worth distinguishing from SCN orbit. Some restaurant-tech contacts overlap with SCN; many do not.

- **PAR alumni network** is largely OUTSIDE SCN. The two clusters intersect mainly through Todd. Don't assume a PAR-alumni contact knows SCN chapter members.
- **McDonald's franchise-side** (Valley Management, Ice Age Management, etc.) is largely OUTSIDE SCN. McD corporate (Bruce, Jeff Coffland) is also outside SCN.
- **Toast / Oracle / NCR / Global Payments** enterprise relationships are mostly OUTSIDE SCN unless an individual is independently active.

When proposing a cross-cluster intro from SCN orbit out to one of these, the recipient genuinely won't already know the SCN contact (and vice versa). High-leverage intro territory.

---

## Maintenance discipline

- Add new heuristics here as Todd surfaces them in chat, rather than burying them in card notes.
- Each heuristic should carry a **source** (Todd-stated date, or "inferred from <evidence>") and a **confidence** (high / medium / low).
- When a heuristic stops being true (cohort breaks up, person leaves cluster, etc.), retire it with a dated note rather than deleting it — the audit trail matters.
- Review quarterly: are any heuristics stale? Are any new patterns visible from accumulated IB / baseline data that deserve to be promoted to a heuristic?

---

*File created 2026-05-12 from operator feedback on the Hospitality Table intro task.*
