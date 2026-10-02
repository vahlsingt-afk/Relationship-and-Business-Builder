# LinkedIn Export Delta — 2026-05-10 (ingested 2026-05-12)

Source file: `Complete_LinkedInDataExport_05-10-2026.zip.zip` (uploaded 2026-05-12). Baseline snapshot taken first: `_snapshots/baseline_index.2026-05-12.json`.

## Headline counts

| | Count |
|---|---:|
| Connections in new export | 2,681 |
| Matched to existing baseline | 2,508 |
| **New connections (added as VC)** | **173** |
| **Disconnections (lost since prior export)** | **10** |
| Reconnections (previously disconnected, now back) | 1 |
| Company changes detected | 163 |
| Role/title changes detected | 251 |

Baseline went from **2,568** entries to **2,741**. RC count unchanged at 20 (17 inner, 2 broader, 1 dormant_valuable). LKI 179, LMI 1,431, VC 1,111 (up from 938).

## RC moves — the high-signal layer

Four inner/broader-tier RCs show LinkedIn moves. These are not noise. Read each one carefully.

### Dave Richards [RC inner, `otp3-leaders`, `former-par-employees`] — PAR Technology → Logic Controls

**Conflict surfaced.** Dave's notes already carry a 2026-05-08 breadcrumb stating *"Was at Logic Controls per LinkedIn; calendar evidence shows currently back at PAR Technology (last meeting 2025-12-29)."* The fresh export reconfirms Logic Controls. So either Dave has stayed at Logic Controls all along and the December calendar invite was a PAR-side meeting he was attending as a vendor/partner, or he's moved again, or LinkedIn lags behind reality.

This matters because Dave is daily-cadence OTP3 cohort, RC inner, and an institutional anchor in your network. The system shouldn't pick — you should. **One direct question to Dave closes it.**

Baseline `current_company` updated to Logic Controls per the LinkedIn-export-is-authoritative rule (ARCHITECTURE.md). All Circle membership and tier state preserved.

### Michael Beck [RC inner, 184 days overdue] — Back to Blue → Inc Tank GTM

Both company *and* title changed (Founder & CEO → Chief Executive Officer). Michael was at Inc Tank GTM in the 2026-01-03 snapshot, then at Back to Blue in the 2026-03-16 export, now back at Inc Tank GTM in the 2026-05-10 export. That's a round-trip in five months. He may be operating across both, or one was a side venture. The relationship was last touched 2025-10-10 — re-engagement was already overdue per yesterday's brief, and now there's a concrete pretext: *"saw you're back at Inc Tank — what changed?"*

Baseline updated. Card needs the breadcrumb in narrative arc next time you touch the card.

### Daran Adair [RC inner, `hospitality-table`] — Ameri-Can Hospitality Consulting ↔ Franchise Grade (CONFLICT)

**Conflict surfaced and held.** The 2026-05-08 baseline correction set Daran's company to Ameri-Can Hospitality Consulting per your input (superseding the LinkedIn-export Franchise Grade reading). The 2026-05-10 LinkedIn export *still* shows Franchise Grade.

Per Tenet 13b (surface conflicts; never guess), I did **not** overwrite Ameri-Can with Franchise Grade. Baseline still reads Ameri-Can. The LinkedIn breadcrumb is recorded in notes. **You need to confirm:** is Daran at Ameri-Can, Franchise Grade, or both? Until you say, the system holds Ameri-Can as canonical.

### Kevin Froese [RC broader, last touch 2026-05-01] — Mobile Insight → T-ROC (The Revenue Optimization Companies)

Title also changed: Chief Revenue Officer (CRO) at T-ROC. You texted him a week and a half ago and he was at Mobile Insight then per baseline. Either he just moved or the LinkedIn lag is short. Worth a one-liner — *"congrats on T-ROC, what's the story?"* — both as relationship hygiene and as a free-of-charge signal-class check.

## LKI moves — worth watching, not all urgent

Thirteen LKI-tier connections show company moves. The notable ones for your domain:

- **Atul Sood** (LKI, connector): OnPoint (formerly ChowCall) → Shackleton AI. Shift from restaurant tech to AI infrastructure. Worth a check-in — Atul has been an active node and the move changes what he can broker.
- **Spencer Amadon** (LKI, `connector`, intro broker): Discover Snacks → VenueByte. Title moved from Co-Founder/CEO to Business Owner — softer language, often signals winding down a co-founded venture or stepping back. Verify before relying on him as an active intro broker.
- **Allie Harrison** (LKI): Southbound → First Light Clothing (Ambassador → Founder). She started her own thing.
- **John Herman** (LKI): Amazon → Johnson Controls Security Products (Strategic Sales Manager — Northeast Region).
- **Khizar Shahid** (LKI): Breeh AI → A+ Active Services (CEO & Founder → Chief Operating Officer). Notable title de-escalation.
- **Sheryar Kayani** (LKI): Clara AI → Symbiotic AI Solutions (Founder → COO). Similar pattern.
- **Jeff Weaver** (LKI): Go Gorilla Marketing → The Community Insiders.
- **WENDY SHORE** (LKI): Content Circle → Wendy Shore & Co | Shore Advice. Pivoting into solo advisory.
- **Jonathan W Pritchard** (LKI): Deep Game Strategy → Jonathan Pritchard. Solo brand consolidation.
- **ryan williams, mba** (LKI): Mobivity → Mistplay. Promotion-adjacent move (Senior BDM II).
- **Max Tsygankov** (LKI): EasyVision → Stealth Startup. New venture in stealth.
- **Amy Krause** (LKI): OSC → Coates Group. Strategic-sourcing-McD lineage.
- **Windy Sebastian-Dean** (LKI): Mastronardi Produce → CNY Preferred Home Inspections (Small Business Owner). Significant industry change.

## Disconnections — 10 entries

LinkedIn connection lost between 2026-03-16 and 2026-05-10. Tagged `linkedin_disconnected_2026-05-12` and noted in `notes`. Signal_class **not** changed — losing the LinkedIn link doesn't reset earned trust.

| Name | Company | Signal class | Notes |
|---|---|---|---|
| **Debora Contreras** | PAR Technology | **LKI** | Most significant. PAR HR Business Partner. LKI-tier loss is real signal. |
| Mark Crompton | Sales Xceleration® | LMI | |
| Jeffrey Layne | Life | LMI | |
| Robert Grimes | IFBTA | LMI | |
| Robin Weiner | Little Caesars Pizza | LMI | |
| Aj Wuthnow | GoTo Foods | VC | |
| Andrew Mask | NextGen QSR | VC | |
| Andy Alan | candypaintjob | VC | |
| Leanne Doyle, MS, CPP | Moraine Park Technical College | VC | |
| Michael Brinkerhoff | Resultant | VC | |

The Debora Contreras drop is the only one worth a moment of thought. Disconnect from a PAR HR partner could be (a) intentional cleanup on her side, (b) a setting change, (c) account deactivation, (d) a misread. Doesn't require action, but it's a one-degree quiet cool of a PAR-side node.

## Reconnections — 1

- **Raj Jenkin** — previously tagged `linkedin_disconnected_2026-03-16`, now present in the new export. Same company (International School of Kuala Lumpur). The `linkedin_disconnected_2026-03-16` tag was removed and a reconnect breadcrumb added.

## New connections — 173

All seeded as VC with `signal_class=VC`, `sources=[linkedin_export_2026-05-10]`, `linkedin_connected_on` populated from the export. Most date-stamped between mid-April and 2026-05-08.

**Shape of the new connections.** Heavy founder/CEO weighting at the top of the role distribution (6 Founders, 6 Co-Founders, 5 Founder & CEO, 3 Chief Executive Officer, 2 CMOs). Very long tail of one-off connections — no single company picks up more than 2 connections. The two with 2 each are **PAR Technology** and **Freelance**.

A handful that may be worth a closer look as you scroll the full new list:

- **Olivia Nielsen** — Head of Revenue @ Perfect Hire (May 8). Recruiting/sales infrastructure.
- **Alex Hult** — CEO @ AIO (May 7).
- **Anthony Niven** — Founder & MD @ Fractional Growth Engine (April 30). Fractional GTM — adjacent to BridgePoint.
- **Javier Rey** — Multi location Manager @ Telefèric Barcelona Restaurant Group (May 2). Multi-unit restaurant ops, international.
- **Kevin Dobson** — Owner @ Kamber Enterprises (May 4).
- Two new PAR Technology connections — worth scanning the full list to see who they are, since PAR-side connections compound your existing 120-person cluster there.

None auto-promote. They sit at VC until evidence appears.

## Role changes — notes on noise vs signal

The raw count of 251 role changes overstates the signal. Most are formatting tweaks LinkedIn introduces between exports — typo fixes (David Pettit "Sofware" → "Software"), case normalizations, optional-suffix additions. The system recorded them all per Tenet 1 (don't invent), but the LKI/RC tier (24 changes) is where to look. One RC-tier role change: Michael Beck (Founder & CEO → Chief Executive Officer at the new Inc Tank GTM role), already captured above.

**One artifact I cleaned up:** Patrick Nelson's role string in the new export reads "Founder & CEO @ Matrix Software Solutions" — same role, just LinkedIn appending the company name to the title field. Reverted current_role to "Founder & CEO" and dropped the breadcrumb. Not a real change.

## What the system did *not* do

- Did not promote any signal class. New connections sit at VC. Promotions happen on evidence, not on the existence of a LinkedIn edge.
- Did not auto-assign Circles to new connections. The Circle taxonomy is operator-driven, not auto-tagged.
- Did not modify `last_touch` for anyone — LinkedIn export doesn't constitute an interaction.
- Did not overwrite the Daran Adair company correction. Held the conflict for you to resolve.

## Open questions to resolve

These belong on a follow-up pass with you:

1. **Dave Richards:** Logic Controls or PAR? Direct text answer settles it.
2. **Daran Adair:** Ameri-Can Hospitality Consulting or Franchise Grade?
3. **Michael Beck:** Inc Tank GTM is now LinkedIn-current. He's also 184 days overdue. The re-engagement note has a built-in pretext now.
4. **Debora Contreras:** worth a one-time check via another channel to know if the disconnect is intentional or incidental.

---

*Delta generated 2026-05-12 from `Complete_LinkedInDataExport_05-10-2026.zip.zip`. Snapshot: `_snapshots/baseline_index.2026-05-12.json`.*
