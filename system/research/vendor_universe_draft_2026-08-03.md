# Draft: Restaurant-Tech Vendor Universe (100-150 target) — for review

**Status:** DRAFT — not yet approved, not yet fed into any pipeline or the graph.
**Purpose:** Phase 1 of the vendor-first baseline project. Once Todd approves/edits
this list, Phase 2 (historical deep research: press releases, earnings reports,
public filings per vendor) runs batch-by-category against this approved list,
with review checkpoints between categories.

**Legend:**
- `[GRAPH]` — already an entity in `system/ecosystem_intelligence.json` (21 vendors currently)
- `[EARN]` — already in `system/earnings_calendar.yaml` (public, SEC-monitored)
- `[ARCHIVE]` — already crawled once in `system/research/restaurant_tech_research_2026-07-21.json`'s archive_registry (case-study pages indexed, mostly unvalidated)
- (no tag) — not yet tracked anywhere in RB; new addition

Categories reuse the taxonomy already defined in `system/restaurant_tech_watchlist.md`
and the `Canonical Tech Stack` workbook tab, so results map directly onto the
existing schema (`vendor_role`, `deployment_status`, `ai_application`, `category`).

---

## POS Platforms (cloud / enterprise / SMB / franchise)
- Toast
- NCR Voyix (Aloha) `[GRAPH: NCR]`
- Oracle Food & Beverage / Simphony (MICROS) `[GRAPH: Oracle]`
- PAR Technology (Brink POS) `[GRAPH][EARN][ARCHIVE]`
- Square for Restaurants
- Clover (Fiserv)
- Lightspeed Commerce (Lightspeed Restaurant / Upserve)
- Revel Systems
- TouchBistro
- Qu `[GRAPH][ARCHIVE]`
- Agilysys (InfoGenesis)
- SpotOn
- Heartland / Global Payments Restaurant POS
- Xenial (formerly Squirrel/HME/Panasonic)
- Squirrel Systems
- Focus POS
- Cake POS (Mad Mobile)
- Talech (Mastercard)
- Rezku POS
- Harbortouch (Shift4)
- ~~NewPOS~~ — REMOVED 2026-08-03: not a vendor. Phase 2 research confirmed
  RB's own graph entity already documents this as McDonald's proprietary
  internal POS system, not a third-party company.
- ~~Radiant Systems~~ — MARKED HISTORICAL 2026-08-03: acquired by NCR in July
  2011 ($1.2B); hasn't existed independently in 15 years. Lineage lives inside
  NCR Voyix/Aloha. Not tracked as its own vendor entity going forward.
- custom/internal POS (per-brand proprietary) `[GRAPH]`

**Open scope question (2026-08-03, unresolved):** Agilysys's named, dated wins
(Marriott, IHG, Mandarin Oriental, casino/resort groups) are hotel/resort F&B
outlets, not standalone restaurant chains. Todd to decide whether hotel-embedded
restaurant F&B counts for this graph before Agilysys evidence is promoted.

## Payments — RESEARCHED 2026-08-03, imported to graph (14 new relationships)
- Fiserv (Clover parent)
- Global Payments (Heartland, Genius) — **now also owns Worldpay** (see below)
- ~~Worldpay (FIS)~~ — CORPORATE STRUCTURE CHANGE, 2026-01-09: Global Payments
  completed a $24.3B acquisition of Worldpay from FIS/GTCR. Worldpay is no
  longer FIS/GTCR-owned; it's a Global Payments-owned brand as of this
  research date. Kept as its own vendor line for now (evidence found still
  refers to it as "Worldpay"), but expect increasing convergence with the
  Global Payments entity going forward.
- Adyen
- Stripe (thin — no dated named restaurant-chain evidence found)
- Shift4 Payments
- ~~TSYS~~ — effectively folded/retired. As part of the same Jan 2026
  transaction, Global Payments divested its Issuer Solutions (former-TSYS)
  business to FIS, now branded "FIS Total Issuing Solutions." No independent
  TSYS-branded entity should be expected going forward.
- Elavon (U.S. Bank) — thin, only tiny independent-restaurant examples found
- Priority Technology Holdings (PRTH) — thin, no dated named-chain evidence
- Paysafe (PSFE) — thin, no dated named-chain evidence
- FreedomPay
- CardFree — acquired by Fiserv 2025-09-05 (folding into Clover); kept as its
  own vendor line since the CardFree product/brand persists through the
  integration
- North / Payment Depot — North is a 2024 rebrand of North American Bancard;
  relationship between "North" and "Payment Depot" (same entity, sibling
  brand, or separate company) unconfirmed, flagged unresolved

## Loyalty / CRM / Guest Data — RESEARCHED 2026-08-03, imported to graph (28 new + 1 enriched)
- Paytronix `[GRAPH][ARCHIVE]` — 8 records, strongest vendor in this batch
- Thanx `[GRAPH]` — 7 records
- PAR Punchh `[GRAPH]` — 3 records
- ~~Fishbowl~~ — renamed from Personica (2023), then acquired by GoTab
  (announced 2026-06-09). Thin evidence beyond the corporate history itself.
- Yumpingo — acquired by Black Box Intelligence (2025-04-28); case-study URLs
  now redirect into BBI's site. 2 records (1 current, 1 historical).
- Como (Como Sense) — thin, no verifiable dated named-brand evidence. A
  secondary-source claim tying it to Burger King's "Royal Perks" could not be
  confirmed against BK's own newsroom and was excluded.
- SessionM (Mastercard) — acquired by Mastercard (2019); per an April 2026
  announcement, now being divested *again* to Capillary Technologies. 1
  historical record (Chipotle).
- Kangaroo Rewards — thin, no verifiable dated named-brand evidence.
- ~~Fivestars (Sunbit)~~ — CORRECTION: this draft's original guess was wrong.
  Fivestars was acquired by **SumUp** in 2021 for $317M, not Sunbit. No
  Sunbit transaction found. Thin evidence beyond the corporate history.
- Preferred Patron — 1 record (Happy Lemon USA, franchisee-scoped).
- ~~LevelUp (Grubhub)~~ — standalone app discontinued Sept 2021, folded into
  Grubhub's product line; Grubhub itself sold to Wonder Group (closed Jan
  2025). 2 records, both written historical/legacy_incumbent (Pret A Manger,
  sweetgreen).
- SevenRooms — acquired by DoorDash for $1.2B (announced May 2025, closed
  June 2025); still operates under its own brand. 2 records.
- Bikky — 3 records. One (Blaze Pizza) hit `entity_resolution_required` and
  was NOT promoted -- a prior Payments-batch record used an overly-long
  compound brand name ("Five Guys and Blaze Pizza (Cypress Five Star
  franchisee, Canada)") that token-collides with the real "Blaze Pizza"
  brand entity, so the importer correctly refused to guess rather than risk
  attaching Bikky's evidence to the wrong entity. Needs manual resolution.
- Craver — 1 thin record (Rook Coffee, Low confidence).

**Process note for future batches:** keep the `brand` field to the actual
restaurant chain name only; put franchisee/region/scope detail in `notes`
instead of folding it into the brand name. A compound brand name creates
token-collision ambiguity for later batches' entity resolution (see Bikky/
Blaze Pizza above).

## Online Ordering / Delivery Enablement — RESEARCHED 2026-08-03, imported to graph (20 new + 2 enriched)
- Olo `[GRAPH][ARCHIVE]` — 6 records, strongest vendor in this batch (Waffle
  House, Five Guys, Denny's, Cracker Barrel, Portillo's, Slim Chickens)
- ChowNow — 2 records. **Now also owns Cuboh** (see below).
- Grubhub for Restaurants — thin (1 record, sourced via a third-party POS
  vendor's press release, not Grubhub's own). Sold by Just Eat Takeaway to
  Wonder Group, deal closed 2025-01-07 ($650M) with a subsequent ~500-person
  layoff; now reads as a generic portal brand rather than a distinctly
  case-studied product.
- DoorDash Storefront — thin, zero usable records. DoorDash doesn't appear to
  publish dated named-customer case studies for Storefront the way
  Olo/Otter/ItsaCheckmate/Deliverect do for their products.
- Uber Direct / Uber Eats for Merchants — 2 records (White Castle, McDonald's
  UK), both confirmed genuine white-label-behind-the-brand's-app
  relationships, not marketplace listings. Product names confirmed current.
- ItsaCheckmate — 3 records, strong (Wendy's 6,000+ locations, Arby's 1,100+
  units, both dated press releases).
- Deliverect — 2 records (Papa John's phased US rollout, Little Caesars
  global).
- ~~Ordermark / Nextbite~~ — CORRECTION: no longer one merged company.
  Ordermark's US business was sold to India's UrbanPiper (2023-06); Nextbite
  (virtual-kitchen licensing) was separately acquired by SBE Hospitality/Sam
  Nazarian around the same time. **Split into two vendor lines going
  forward.** 1 thin record found under the old combined name (Nathan's
  Famous) — needs re-attribution to whichever successor entity actually
  applies once split.
- Slice — thin, zero usable records. Business model targets independent
  single-location pizzerias almost exclusively; no genuine multi-unit chain
  customer found.
- ~~Bbot (DoorDash)~~ — appears fully absorbed into DoorDash's Platform
  Services suite; zero post-2021-acquisition Bbot-branded customer evidence
  found. Likely no longer a standalone brand worth tracking separately.
- Snackpass — 2 records (Pokemoto, Presotea).
- ~~Cuboh~~ — acquired by ChowNow, announced 2024-03-28. Thin, zero usable
  records under either independent or ChowNow ownership.
- Otter — 3 records (Skyline Chili, McDonald's [franchisee-scoped], Just
  Salad).
- Popmenu — 2 records (Max's Restaurant, Kelly Companies).

**One entity-resolution ambiguity, unresolved:** ChowNow/Yolk was NOT
promoted — "Yolk" is short/generic enough to collide with an existing
entity name; needs manual resolution, same pattern as the Bikky/Blaze Pizza
case in the Loyalty batch.

## Drive-Thru / Voice AI / Conversational — RESEARCHED 2026-08-03, imported to graph (29 new + 1 enriched)
- Presto (Presto Voice) `[GRAPH][ARCHIVE]` — grew beyond the existing
  Checkers & Rally's record: added Carl's Jr./Hardee's (May 2023 CKE pilot)
  and Taco John's (2-location pilot).
- Hi Auto `[GRAPH]` — grew beyond Checkers & Rally's: added Popeyes UK
  (all-locations, strongest record in the batch), Bojangles, Lee's Famous
  Recipe Chicken, Burger King New Zealand.
- SoundHound AI `[EARN]` — 15 records, by far the deepest vendor in this
  batch. Best source: Q3 2025 earnings release (2025-11-06) directly naming
  full-deployment wins at MOD Pizza, Habit Burger, Red Lobster, Torchy's
  Tacos, plus franchise wins at Firehouse Subs, Five Guys, McAllister's Deli.
- ~~Valyant AI (acquired by SoundHound)~~ — CORRECTION: this draft's premise
  was wrong. No SoundHound-Valyant transaction exists in any source checked.
  The actual acquirer was **ConverseNow**, announced 2024-07-09. Valyant's
  pre-acquisition evidence (Carl's Jr./Hardee's pilot) kept under its own
  vendor line.
- ConverseNow `[ARCHIVE]` — 6 records (Domino's 1,400 locations/80% of NA
  phone orders is the standout; also now owns Valyant AI, see above).
- Coates Group (drive-thru digital signage/comms) `[ARCHIVE]` — 3 records,
  all McDonald's ANZ / Hungry Jack's (Australia/NZ market specifically).
- HME (drive-thru headsets/timers) — 6 records, but all dated 2010-2016
  ("preferred vendor" style relationships); current status unconfirmed,
  written as historical_current_status_not_reconfirmed.
- Malcolm AI — **zero evidence found anywhere.** Flag for verification: this
  may not be a real company, or the name may be misremembered/wrong from
  when this draft list was compiled. Recommend Todd confirm or drop.

**Entity-dedup cleanup done alongside this batch:** 6 duplicate brand
entities from earlier batches (created by compound names like "Applebee's
(Dine Brands Global)") were merged into their canonical plain-name
counterparts using a new `merge-brand-entities` CLI command, since they were
blocking this batch's plain "Carl's Jr."/"Five Guys"/etc. rows via
entity-resolution ambiguity. Also renamed "CKE Restaurants Holdings
(Hardee's and Carl's Jr.)" to just "CKE Restaurants Holdings" (kept as its
own entity, not merged, since its 2 relationships are genuinely parent-level
deals covering both brands jointly, not attributable to just one).

## Computer Vision / Robotics / Kitchen Automation — RESEARCHED 2026-08-03, imported to graph (6 new + 1 historical)
- Miso Robotics (Flippy) — 7 records, deepest vendor in this batch (White
  Castle, Jack in the Box, Buffalo Wild Wings, Chipotle [discontinued],
  Wing Zone, Panera Bread, Insert Coin). Financially thin company (SEC Reg A
  filings show reliance on continuous investor financing) — verify before
  treating any deployment as durable. Real PR-vs-reality gap found: a 2022
  press release announced "100 new locations" at White Castle, but an
  independent Jan 2025 trade-press report found only 13 total installed
  locations across White Castle + Jack in the Box combined.
- ~~Picnic (pizza assembly robotics)~~ — **shut down entirely 2026-05-11**
  (General Assignment for the Benefit of Creditors, assets liquidated). 2
  historical records kept (Moto Pizza, Speedy Eats) but the vendor itself no
  longer exists.
- Chef Robotics — thin, zero restaurant-chain records. Real, well-funded
  company, but every named customer (Amy's Kitchen, Sunbasket, Cafe Spice,
  Chef Bombay) is a food *manufacturer*/meal-kit producer, not a restaurant
  chain; QSR entry is explicitly future-tense in the company's own materials.
- Nala Robotics — 1 record (Re-Up, an AI convenience-store/gas-station
  chain — flagged as a borderline restaurant-category fit).
- Mashgin (computer-vision checkout/kiosk) — thin, zero restaurant-chain
  records. Large/well-funded, but named deployments are convenience stores
  (Circle K/Couche-Tard), stadiums, and cafeterias.

**Discontinued deployment confirmed:** Chipotle's "Chippy" tortilla-chip
robot (Miso Robotics) — then-CEO Brian Niccol confirmed on record (March
2024) it "didn't pan out." Written to the graph as historical, not active.

## Kiosks — RESEARCHED 2026-08-03, imported to graph (9 new)
- Grubbrr — 4 records, strongest vendor in this batch (Bojangles, PDQ,
  BurgerFi, Dave's Hot Chicken).
- ~~Toshiba Global Commerce Solutions~~ — thin, zero usable records. Heavily
  grocery/retail/healthcare focused; Toshiba Corp itself went private (JIP
  consortium, delisted Dec 2023) but TGCS continues operating under its own
  name with no ownership change confirmed.
- ~~Zivelo~~ — acquired by Verifone, 2019-09-02. No longer independent. 3
  records kept under its own line (McDonald's, Sonic Drive-In, Thai Chili 2
  Go).
- Olea Kiosks — 2 thin/pilot-scale records (Habit Burger Grill, CEFCO).
- ~~KIOSK Information Systems~~ — thin, zero usable records. Same pattern as
  Toshiba: retail/healthcare/HR focused, no restaurant customer found.
- ~~Nextep Systems~~ — acquired by SICOM (2018), itself acquired by Global
  Payments (2018, $415M) and folded into **Xenial** (the same Xenial already
  listed under POS Platforms, currently thin there). 2 records (Wow Bao,
  Moe's Southwest Grill, both pilot/franchisee scale).
- ~~Appetize (venue/stadium POS+kiosk)~~ — CORRECTION: this draft's premise
  (Legends, 2022) is wrong. Actual chain: SpotOn acquired Appetize (2021,
  $415M), then **Shift4 Payments** acquired SpotOn's sports/entertainment
  division including Appetize (2023-10-02, ~$108.7M). Appetize is
  Shift4-owned. Business is fundamentally stadium/venue/institutional, not
  restaurant chains — only 1 genuine restaurant-chain record found (Hale &
  Hearty).

**Cross-reference note:** Nextep/Xenial and Appetize/Shift4 both point back
to vendor lines already in the POS/Payments categories — when those
categories get revisited, check whether any Xenial or Shift4 evidence found
there should also carry a "formerly Nextep"/"formerly Appetize" alias.

## Digital Menu Boards / In-Store Display — RESEARCHED 2026-08-03, imported to graph (8 new)
- Coates Group — already covered under Drive-Thru/Voice AI (3 records:
  McDonald's ANZ x2, Hungry Jack's); not re-researched here.
- Mood Media — 6 records, mostly small/regional chains (Fryday Romania,
  Wagamama UK airport terminals, McDonald's via a Romanian licensee — none
  brand-wide). Filed Chapter 11 in 2020, emerged, acquired by Vector Capital
  (private equity), completed 2021-01-07 — now privately held. Several
  bigger-name Mood Media case studies (Wendy's, Burger King, Moe's, Caribou
  Coffee, Jamba) were checked and excluded: they document Mood's
  music/audio business, not digital signage.
- Rise Vision — 2 thin/small records (Famous Famiglia at two airport
  locations via a reseller, Kamado Grille single-location). A false lead
  (claimed McDonald's/Starbucks case studies) was checked against the
  primary source and found to be generic third-party statistics, not real
  Rise Vision customer claims — correctly excluded rather than included.

## KDS / Kitchen Ops Execution — RESEARCHED 2026-08-03, imported to graph (27 new + 3 updated). Unusually heavy corporate churn: 4 of 5 vendors changed hands.
- ~~QSR Automations (ConnectSmart KDS)~~ — Battery Ventures majority
  investment (2024-11-01), then merged into **Crunchtime** (mid-2025);
  ConnectSmart Kitchen/Host products literally renamed "Crunchtime
  Kitchen"/"Crunchtime Host." 5 records (Boston Pizza, Brinker
  International, Dave's Hot Chicken, Cheesecake Factory, CKE Restaurants
  Holdings [historical]). Future evidence for this product line belongs
  under Crunchtime (Back Office/Inventory category) going forward.
- Fresh KDS — thin, 3 low/medium-confidence records (Taziki's, Bonchon,
  Smashburger UK master-franchisee). Only vendor of the five still
  independent.
- ~~Delaget (franchise ops analytics)~~ — acquired by PAR Technology Corp
  ($132M, closed 2024-12-31), rebranded **"PAR OPS"** (with "Coach"/"Coach
  AI" sub-products). 6 records, mostly individual Taco Bell/KFC franchisee
  operators (MAS Restaurant Group, Diversified Restaurant Group) rather
  than brand-corporate deals — scored franchisee_deployment accordingly.
- ~~Meazure Up~~ — combined with ComplianceMate and Storewise under a new
  parent brand, **"Ladle"** (Nexa Equity), launched 2025-03-04. 10 records,
  richest vendor in this batch (Krystal, Golden Corral, Pizza Ranch,
  Osmow's, Papa Gino's, D'Angelo, LaRosa's, Kernels, The Keg, Spur Steak
  Ranches).
- ~~Jolt (checklists/ops execution)~~ — acquired by Digi International
  (NASDAQ: DGII, ~$145.5M, closed 2025-08-18), folded into Digi's
  SmartSense unit but keeps its own brand name. 6 records (Casey's, Dave &
  Buster's, Main Event, Chick-fil-A, Culver's, Buffalo Wild Wings
  [single franchisee]).

**Research-backlog correction found:** two rows in the 2026-07-21 candidate-
evidence archive were mis-tagged under Delaget via keyword collisions
("Pizza Ranch" was actually a Taco Bell/DRG case study; "Daily Grill" was
actually referencing DailyPay, an unrelated company). Pizza Ranch turned out
to be a genuine Meazure Up customer instead, found independently.

## Back Office / Inventory / Supply Chain / Accounting — RESEARCHED 2026-08-03, imported to graph (30 new)
- Restaurant365 `[GRAPH]` — already had 4 relationships; added 8 more, almost
  all explicitly franchisee-scoped except Black Rock Coffee Bar
  (160+ corporate-owned locations, brand-wide). **Now also owns Compeat**
  (see below).
- Crunchtime `[GRAPH]` — already had 6 brands; added exactly the new-ground
  Zenput product line: Wingstop (brand-corporate, 2,000+ locations) and
  Tacala Companies' 300+-unit Taco Bell franchisee rollout. QSR Automations
  evidence stays under its own vendor line (see KDS/Kitchen Ops category).
- MarginEdge — 2 records (Burger 21, Sunday in Brooklyn).
- Apicbase — 3 records, all European/UK chains (Heavenly Desserts,
  Restaurant Company Europe, Bright Kitchen).
- Fourth (HotSchedules/Adaco) — 2 records (Chili's Grill & Bar, Noodles &
  Company). Confirmed: HotSchedules+Fourth merged 2019, renamed "Fourth
  Enterprises, LLC" Nov 2020; HotSchedules persists as the product brand.
- Craftable (Bevager/Foodager) — 1 record (TC Restaurant Group).
- ~~Compeat~~ — CORRECTION: this draft's premise (PAR Technology, 2019) is
  wrong. No PAR-Compeat transaction exists in any source checked; Compeat
  was acquired directly by **Restaurant365**, announced 2021-06-10/14. Now
  effectively an R365-owned product line — likely why almost no
  post-2021 Compeat-branded evidence exists. 1 pre-acquisition record kept
  (Taffer's Tavern, historical).
- ~~Yellow Dog Inventory~~ — thin, zero usable restaurant-chain evidence;
  only single-property hotel testimonials found.
- BlueCart — 2 records (Jimmy John's, Neighborhood Restaurant Group), both
  historical/unconfirmed-current.
- Galley Solutions — 1 low-confidence record (&pizza) — **held back from the
  graph**, entity-resolution ambiguous (the "&" strips out during
  normalization, leaving "pizza" as a token that collides with multiple
  existing pizza-brand entities). Needs manual resolution if pursued further.
- ~~Ottimate (formerly Plate IQ — AP automation)~~ — 2023 rename from Plate
  IQ confirmed settled. 2 records, both multi-concept operator groups
  (Thrive Restaurant Group, Prime Steak Concepts) rather than named chains.
- Nory — 5 records, UK-focused (Black Sheep Coffee, Pieminister, Rocksalt,
  Tasty African Food, CUPP).
- xtraCHEF (Toast) — 2 records (Dos Toros Taqueria, ThinkFoodGroup).
  Confirmed fully Toast-owned since 2021; researched as its own product
  line per the established Xenial/Nextep/Appetize convention.
- ~~Zenput~~ — folded into the Crunchtime entry above (already part of
  Crunchtime per the Kiosks-category finding); not researched separately.

## Labor / Workforce — RESEARCHED 2026-08-03, imported to graph (17 new)
- 7shifts — 8 records, strongest vendor in this batch (Pizza Ranch,
  National Coney Island, The Human Bean, Clean Juice brand-wide/significant;
  Black Rock Coffee Bar, Jamba, Smoothie King, Steak 'n Shake franchisee-scoped).
- HotSchedules (Fourth) — already covered in Back Office/Inventory (2
  records); not re-researched here.
- ~~When I Work~~ — thin, zero usable records (only single-location
  independents on their own case-study page).
- Homebase — 1 thin record (Luxe Bites, 4 locations). **Caution, not a real
  change:** a "Homebase acquired 2025" search initially returned an
  unrelated UK DIY retail chain also named Homebase (bought out of
  administration by CDS Superstores/The Range) — the actual vendor
  (joinhomebase.com, SF-based) is unaffected and remains independent.
- ~~Sling~~ — acquired by **Toast**, announced 2022-07-07; now "Sling by
  Toast." Thin, zero usable records beyond bare unbacked customer logos
  (Subway, Taco Bell) with no dated case study.
- Push Operations — 5 records (Crumbl, Earls Kitchen + Bar, Booster Juice,
  Freshii, Village Ice Cream). One marketing claim ("700+ locations" at ZZA
  Hospitality) didn't reconcile with the operator's own site (~15 locations)
  — excluded rather than promoted on faith.
- Deputy — 3 records (Juice Press, Crust Pizza, Instamaki). Confirmed still
  independent/private (Sydney HQ), reportedly crossed $1B valuation 2024.
- Legion Technologies — thin, zero usable records. Real restaurant case
  studies exist but every one anonymizes the brand name, making them
  unusable given the schema requires a named brand. Confirmed still
  independent (raised $50M debt Dec 2024 + earlier $50M equity).

## Unified Commerce / Enterprise Integration Layer — REVIEWED 2026-08-03, no new research needed
All three entries (PAR Data Central/Engagement Cloud, NCR Voyix commerce
platform, Oracle Food & Beverage platform) are the same vendors already
deeply researched under POS Platforms — this category was drafted as a
"same vendor, different angle" note, not a distinct set of companies.
Skipped as a research batch; no genuinely new vendor here.

## Training / LMS — RESEARCHED 2026-08-03, imported to graph
- Opus Training — 5 records (Five Guys franchisee, Newk's Eatery,
  Smashburger, PLANTA, Just Salad). Name-collision caught and avoided: a
  second, unrelated company also called "Opus Training" sells
  sexual-harassment compliance training — verified the restaurant-tech
  vendor (opus.so) before writing anything.
- Schoox — 3 records (Subway 44,000+ location 2016 global rollout, Checkers
  & Rally's, Tropical Smoothie Cafe). Received Vista Equity Partners growth
  investment (2021), remains independent.

## Franchise Management Platforms — RESEARCHED 2026-08-03, imported to graph
- FranConnect — 6 records. **Acquired RizePoint** (quality-management
  platform), 2024-02-27; 3 records (Arby's, Blaze Pizza, Friendly's) are
  pre-acquisition RizePoint relationships now hosted on franconnect.com,
  flagged historical since current status under FranConnect ownership is
  unconfirmed.
- Naranga — 3 records (Pizza Factory brand-wide, Arooga's, HoneyBaked Ham
  [specialty prepared-food retail, not a classic restaurant — flagged for
  review]). Naranga's own domain was unreachable during research (DNS
  failures), so evidence relies entirely on third-party coverage — worth a
  follow-up pass if that becomes reachable.

**Research complete.** All 14 categories in this vendor universe have now
been researched and imported (except Unified Commerce, reviewed as
redundant with POS — see above).

## Backlog rescore (2026-08-04/05)

The 936-row `candidate_evidence` array from the 2026-07-21 research run
(covering 8 vendor archives: r365, crunchtime, paytronix, par, thanx, olo,
qu, coates) was cross-referenced against everything the fresh vendor-first
pass found. 675 rows were type "Mention candidate" (low-value, mostly
blog-post noise per the Loyalty batch's earlier finding) and skipped. Of the
261 "Relationship candidate" rows, 48 were already covered by fresh
research; the remaining **213 were split into two batches and actually
opened/verified page-by-page** (not just re-searched):

- **Batch A** (Crunchtime/PAR/Restaurant365, 120 rows): 41 confirmed real
  records, 1 dead link, 74 rejected as pattern-matching noise (word
  collisions like "press" → Pizza Press, "award" → Ward's Restaurant).
  Imported (one row skipped: a Torchy's Tacos/PAR-Technology loyalty claim
  that duplicated an existing PAR Punchh relationship under a different
  vendor-entity name — see vendor-dedup note below).
- **Batch B** (Olo/Paytronix/Thanx/Qu, 93 rows): 46 confirmed real records
  (collapsed from 49 rows with corroborating duplicates), 0 dead links, 44
  rejected as noise. Imported cleanly.

**Second vendor-entity dedup found and fixed:** "PAR Technology" and "PAR
Technology (Brink POS)" had ended up as two separate vendor entities from
different batches, causing `check_relationship_conflict()` to see false
rival-vendor conflicts (Papa Johns, Big Chicken) that weren't real —
extended `merge-brand-entities` to handle either side of a relationship
(vendor duplicates via `to_entity_id`, not just brand duplicates via
`from_entity_id`) and merged them. Regression test added.

**Overall backlog verification yield: 87 of 213 unverified candidates
(41%) turned out to be real; 59% were pattern-matching noise** — a useful
data point on how much to trust an unvalidated crawl-based candidate list
going forward.

---

## Notes for review

1. **Count as drafted: ~110 distinct companies** (some appear in two categories
   by design — e.g. Coates Group is both drive-thru and digital-menu-board;
   PAR/NCR/Oracle span POS + unified commerce). Real count of *distinct legal
   entities* is closer to ~100. Tell me if you want this padded toward 150 or
   trimmed toward the tighter 100 end.
2. **Known blind spots I'd flag before research starts:**
   - Regional/international POS players (e.g. UK/EU: Epos Now, Lightspeed's
     European base) — included Lightspeed but not gone deep on EU-only names.
     Confirm if international vendors are in scope or U.S.-focused only.
   - Hardware-only players (Verifone, Ingenico, Elo Touch, Panasonic terminals)
     are largely omitted — POS hardware today is mostly bundled with software
     vendors above. Add a dedicated hardware pass if you want that as its own
     category.
   - A few names above are genuinely uncertain fits as of 2026 (some may have
     been acquired, rebranded, or shut down since I last have confirmed
     information) — Phase 2 research will surface and correct these, not
     assume they're all still independent, current entities.
3. **Existing raw material to reuse, not redo:** the 2026-07-21 research run
   (`system/research/restaurant_tech_research_2026-07-21.json`) already crawled
   ~15-20 vendor case-study archives and produced 936 candidate-evidence rows
   and 261 relationship-pattern candidates, of which only 38 were ever promoted
   to verified. Phase 2 should start by re-scoring those 936 unvalidated rows
   against the categories/vendors above before doing fresh web research —
   cheaper than re-discovering what's already been indexed once.
