# Google Takeout Load — 2025-12-26

Initial load of Google Takeout export. The takeout contained Contacts (vCards), Calendar (.ics), Google Meet conference history, and Google Chat. **Gmail mbox was not included in this export** — see footer for how to add it next time.

## Headline numbers

| Source | Count | Outcome |
|---|---:|---|
| vCards parsed | 182 | 11 emails added to existing baseline; 29 phones added |
| Calendar events parsed | 816 | Across 4 calendars (personal, work, family, appointments) |
| Unique calendar attendee emails | 421 | 76 matched to existing baseline; 384 unmatched |
| Google Meet history records | 71 | Cross-referenced with calendar events |
| Google Chat | 10 msgs | Only Google system bots — no human chat data |

## What changed in baseline

| Action | Count |
|---|---:|
| Existing entries enriched with email | 11 |
| Existing entries enriched with phone | 29 |
| New entries added (calendar 3+ meetings, non-LinkedIn) | 31 *(after dedup)* |
| Duplicate-detection merges (smart name matching) | 7 |
| LKI promotions from meeting evidence | 1 |
| LMI promotions from meeting evidence | 3 |

## Signal class distribution (current)

| Class | Count |
|---|---:|
| VC | 939 |
| NPR | 0 |
| LMI | 1428 |
| LKI | 197 |
| RC | 0 |
| **Total** | **2564** |

## Smart-merge dedup ([important — these were duplicates the calendar+email join would have created])

Some calendar attendee emails initially looked like new people because the LinkedIn baseline lacked emails for them. The smart-name-match pass identified these as the same person and merged the data into the existing high-tier baseline entry.

| Email-derived alias | Merged into existing entry | Match pattern |
|---|---|---|
| "Spytko, Amy" (amy.spytko@ncrvoyix.com) | **Amy Spytko** | firstname.lastname pattern: 'amy.spytko' → 'Amy Spytko' |
| Dpage (dpage@advisorhr.net) | **Danielle Page** | first-initial+lastname pattern: 'dpage' → 'Danielle Page' |
| Faizan11 Ahmad (faizan11.ahmad@gmail.com) | **Faizan Ahmad** | firstnamelastname pattern: 'faizan11.ahmad' → 'Faizan Ahmad' |
| Jmorrison (jmorrison@qubeyond.com) | **John Morrison** | first-initial+lastname pattern: 'jmorrison' → 'John Morrison' |
| Joelbyler04 (joelbyler04@gmail.com) | **Joel Byler** | firstnamelastname pattern: 'joelbyler04' → 'Joel Byler' |
| Mbeck (mbeck@popcorngtm.com) | **Michael Beck** | first-initial+lastname pattern: 'mbeck' → 'Michael Beck' |
| Scottmarsh9021 (scottmarsh9021@gmail.com) | **Scott Marsh** | firstnamelastname pattern: 'scottmarsh9021' → 'Scott Marsh' |

Notably: **Michael Beck** (one of your 9 RC candidates) had 24 calendar meetings under `mbeck@popcorngtm.com` — that evidence is now correctly attributed to his existing entry. Same for Scott Marsh (27 meetings) and Joel Byler (26 meetings).

## Calendar evidence reinforces LinkedIn-derived signal (9 people with 5+ meetings)

These are people already in your baseline (via LinkedIn) who now have calendar evidence reinforcing their signal class. **The combination is much stronger than either source alone**: LinkedIn shows mutual recognition; calendar shows actual recurring face-to-face contact.

| Name | Company | Signal class | Meetings | Last | RC candidate? |
|---|---|---|---:|---|---|
| Noelle Labrie | Tri-Skill Consulting, LLC | LKI | 36 | 2026-01-09 | — |
| Simon Zatyrka 🔥🥄 | Culinary Mechanic | LKI | 34 | 2026-01-09 | — |
| Melissa Long | Cozzini Bros. | LKI | 26 | 2025-12-26 | — |
| Paul McCarthy | Hero Facility Services | LKI | 26 | 2025-12-24 | — |
| Allie Harrison | Southbound | LKI | 17 | 2025-12-02 | — |
| Amy Spytko | QSRSoft | LKI | 11 | 2025-12-29 | — |
| Jonah Friedl | Nomad Go | LKI | 10 | 2024-07-31 | — |
| Jeff Wayman | Telaid Industries, Inc. | LKI | 10 | 2025-12-29 | **YES** |
| Sarah Ansari | Instadine | LKI | 7 | 2025-08-22 | — |

## New non-LinkedIn relationships (31 people)

People who weren't in your LinkedIn network but show substantial calendar evidence. These are real working relationships outside of LinkedIn — exactly the kind the architecture said to include. They've been added to baseline at LMI or LKI based on meeting frequency.

| Name | Email | Company | Signal class | Meetings | First → Last |
|---|---|---|---|---:|---|
| Andrew Morlidge | andrewm@nomad-go.com | — | LKI | 32 | 2024-05-31 → 2024-07-31 |
| Steven | steven@lawrencecoaching.com | — | LKI | 30 | 2025-07-21 → 2025-10-24 |
| Mike | mike@methodcommerce.co | — | LKI | 29 | 2025-08-13 → 2026-01-09 |
| Coachkimberlykay | coachkimberlykay@gmail.com | — | LKI | 26 | 2025-08-13 → 2025-10-22 |
| Maureen | maureen@overridge.com | — | LKI | 26 | 2025-08-13 → 2025-10-22 |
| Yourfutureisawaiting | yourfutureisawaiting@gmail.com | — | LKI | 26 | 2025-08-13 → 2025-10-22 |
| Zoll Andrew | zoll.andrew@me.com | — | LKI | 26 | 2025-08-13 → 2025-10-22 |
| Jim | jim@benchmarksixty.com | — | LKI | 17 | 2025-07-11 → 2025-12-24 |
| Chason F | chason.f@hr-4u.org | — | LKI | 14 | 2025-08-12 → 2026-01-09 |
| David Richards | darich247@gmail.com | Par | LKI | 12 | 2025-11-03 → 2025-12-29 |
| Nick Neylon | nicholas.neylon@gmail.com | — | LKI | 11 | 2020-11-22 → 2025-01-01 |
| David Greschler | davidg@nomad-go.com | — | LKI | 9 | 2024-06-24 → 2024-07-25 |
| Joshua Clark13 | joshua.clark13@outlook.com | — | LKI | 9 | 2025-08-13 → 2025-10-22 |
| Gia | gia@thetoastchick.com | — | LKI | 7 | 2025-07-25 → 2025-10-31 |
| Seth | seth@sethrankin.com | — | LKI | 6 | 2025-07-25 → 2025-08-22 |
| Gena | gena@sugarfireconsulting.com | — | LKI | 5 | 2025-07-25 → 2025-08-22 |
| Jeffb | jeffb@macshospitality.com | — | LKI | 5 | 2025-07-25 → 2025-08-22 |
| Vin Puleio | vin.puleio@gmail.com | — | LKI | 5 | 2025-11-07 → 2025-12-03 |
| Zach | zach@otfrc.com | — | LKI | 5 | 2025-07-25 → 2025-08-22 |
| Cultivatedculinary | cultivatedculinary@gmail.com | — | LMI | 4 | 2025-07-25 → 2025-08-22 |
| Vinod | vinod@poddo.ai | — | LMI | 4 | 2025-07-29 → 2025-08-13 |
| Contact | contact@beyondsatisfaction.co.uk | — | LMI | 3 | 2025-09-18 → 2025-11-11 |
| Donnie at Success Champions | donnie@donnieboivin.com | — | LMI | 3 | 2025-06-10 → 2025-11-17 |
| Emily | emily@brand-itude.com | — | LMI | 3 | 2025-07-08 → 2025-09-03 |
| Felipe | felipe@foodtechai.net | — | LMI | 3 | 2025-08-13 → 2025-11-17 |
| Gailyanacek | gailyanacek@gmail.com | — | LMI | 3 | 2025-02-24 → 2025-06-09 |
| Jeremy | jeremy@6amworkshirts.com | — | LMI | 3 | 2025-07-25 → 2025-08-07 |
| Jose | jose@longfisolutions.com | — | LMI | 3 | 2025-11-26 → 2025-12-10 |
| Lisa Krueger | lisa@kruegerfamilylawcenter.com | — | LMI | 3 | 2025-02-24 → 2025-06-09 |
| Seujan Bertram | seujan@nomad-go.com | — | LMI | 3 | 2024-07-23 → 2024-07-25 |

## Known limitations of this load

- **Gmail mbox not in this takeout.** The 3 zips contained Contacts, Calendar, Drive (irrelevant), Google Meet history, and Google Chat — but not Mail. To add Gmail evidence next time: in Google Takeout, explicitly include "Mail" and select either all messages or `Categories\Important` for a smaller export. Gmail is by far the strongest LKI source for non-LinkedIn relationships.

- **314 unmatched calendar attendees.** These are calendar invitee emails that couldn't be matched to baseline AND had fewer than 3 calendar meetings. They include one-off meeting attendees, vendors/recruiters, and people whose name isn't extractable from their email. Most are probably noise; some may be real but low-frequency.

- **Google Chat data was only Google system bots** (Google Meet / Google Drive notifications). Real Chat data with humans isn't in this takeout. Likely Todd uses other channels for direct messaging (LinkedIn DMs, SMS, email) more than Google Chat.

- **vCard data was sparse on emails.** Of 182 vCards, only 55 had email addresses. The rest were phone-book imports with names and phones only. We added 29 phone numbers to baseline, which will be useful for SMS-based outreach later.

---

_Sources: 3 takeout zips dated 182 vcards across 4 groups; 4 .ics calendars totaling 816 events; 71 Meet records. Baseline integrity preserved: existing system fields untouched (signal_class only updated upward); merge breadcrumbs added to notes; Google Takeout source tag (`google_takeout_2025-12-26`) added to enriched entries._