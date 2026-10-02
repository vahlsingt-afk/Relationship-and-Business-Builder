# Franchisee Finder — Feature Specification
*Status: Proposed (not started) · Author: Todd Vahlsing (feature request, pasted 2026-10-01) · Captured by: Claude · Date: 2026-10-01*

**Note on prior art:** `system/franchisee_hierarchy.json` already exists as a static, one-time
extract (Franchise Times "2026 Restaurant 200," operator → brand → unit-count only, no legal
entities, no confidence model, no evidence/provenance, no refresh cycle). Franchisee Finder as
specified below supersedes it as a canonical, evidence-backed, continuously-refreshed RBB data
domain. Treat the existing JSON as a candidate seed/import, not as the schema to build on.

This document is a direct capture of Todd's feature request. It has not yet been scoped against
the live `system/api/server.py` operation set, `SCHEMAS.md`, or `CANONICAL_REGISTRY.yaml` — that
scoping (what new operations, what storage shape, how it slots into the existing
account/contact/competitor data model) is the next step before implementation, per
`ARCHITECTURE.md` / `DATA_TIER_ARCHITECTURE.md` conventions.

---

## 1. Objective

Build a new RBB intelligence capability called **Franchisee Finder**: a research-backed database
of restaurant franchisee organizations — their brand portfolios, unit counts, headquarters,
ownership structures, leadership/contact information, geographic footprints, and related
public-record intelligence.

Designed as an **evidence-based ownership-resolution system**, not a simple franchisee directory.

Core questions it must answer:
- Who are the franchisees of a restaurant brand?
- How many units does each franchisee operate?
- What other restaurant brands does that organization operate?
- What legal entities belong to the same operating organization?
- Where is the franchisee organization headquartered?
- Who owns or controls the organization?
- Who are its relevant executives and business contacts?
- Where does it operate?
- How confident are we in each assertion?
- When was each assertion last verified?
- What public evidence supports it?

Available through both the RBB Team Portal and individual-user RBB interfaces.

---

## 2. Architecture Principle

Franchisee intelligence is its own **canonical RBB artifact/data domain** — not buried inside
brand records, and not an isolated Team Portal-only database.

The artifact must be:
- persistent, structured, evidence-backed, independently refreshable
- queryable by RBB
- accessible through the Team Portal and to authorized individual RBB users
- linkable to existing brands, contacts, accounts, opportunities, and intelligence
- capable of representing parent companies, operating companies, legal entities, and the
  relationships between them

The Team Portal is an **interface into** this intelligence, not the canonical data store.

---

## 3. Core Entity Model

- **Franchisee Organization** — the actual operating organization/economic entity (e.g. "ABC
  Restaurant Group").
- **Legal Entities** — individual LLCs/corporations/subsidiaries associated with that
  organization (e.g. ABC Taco LLC, ABC Chicken LLC, ABC Restaurants Wisconsin LLC).
- **Brand Relationships** — brands operated by the organization, with identified unit counts
  (e.g. Taco Bell — 84 units; KFC — 27; Arby's — 16).
- **Locations** — individual restaurant locations where sufficiently reliable public information
  exists.
- **People** — publicly identified executives, owners, technology leaders, operations leaders,
  and other relevant business contacts.

The system must **not** automatically assume similarly named legal entities represent the same
franchisee organization. Entity relationships require evidence and confidence.

---

## 4. Initial Research Cycle — ChatGPT

ChatGPT Deep Research performs the initial franchisee intelligence research, attempting to
establish the franchisee universe across the RBB restaurant-brand universe.

Prioritized public sources:
1. Franchise Disclosure Documents, particularly Item 20
2. Franchisor websites
3. Franchisee/operator websites
4. SEC filings where applicable
5. State corporate/business records
6. Company announcements
7. Franchisor announcements
8. Acquisition/development agreement announcements
9. Credible restaurant/franchise trade publications
10. Credible local business reporting
11. Franchise associations
12. Public executive/company profiles
13. Conference/speaker biographies
14. Job postings and other corroborating public business information

Research must stay based on publicly available business information and public records. Do not
intentionally collect private, leaked, purchased, credential-gated, or otherwise non-public
personal information.

---

## 5. ChatGPT Research Output

ChatGPT produces **structured evidence packets** suitable for machine processing — it does not
directly modify canonical Franchisee Finder records.

Each researched assertion should include, where applicable: franchisee/operator name, proposed
canonical organization name, legal entity name, parent organization, relationship type, brand,
identified unit count, unit-count basis, headquarters, registered/legal address (when relevant),
operating geography, ownership structure, public/private status, ownership/investment
relationships, named executive/contact, executive title, publicly available business contact
information, source, source URL, publisher, publication/document date, research/access date,
evidence excerpt or supporting observation, confidence, confidence rationale, known conflicts,
limitations, last verified date.

ChatGPT should preserve uncertainty rather than force conclusions.

---

## 6. Confidence Model

Confidence exists at the **assertion/relationship level**, not merely the franchisee-record
level. Example — different claims about the same organization can carry different confidence:

- ABC Restaurant Group headquarters → Dallas, Texas — 95%
- ABC Restaurant Group → owns ABC Taco LLC — 82%
- ABC Restaurant Group → operates 84 Taco Bells — 91%
- ABC Restaurant Group → controls payments technology selection — 45%

Preserve useful low-confidence intelligence rather than discard it. Low confidence must be
clearly distinguishable from established fact.

---

## 7. Codex Processing Responsibility

Codex processes ChatGPT's research output into RBB: schema validation, evidence validation,
entity resolution, duplicate detection, organization-name normalization, legal-entity
normalization, relationship creation, brand matching against canonical RBB brands, contact
matching against existing RBB contacts, unit-count reconciliation, confidence processing,
conflict detection, provenance preservation, change detection, artifact creation/update,
research-queue management.

ChatGPT discovers and documents evidence. Codex determines how that evidence maps safely into
the RBB data model. **Codex must not silently convert ambiguous research into confirmed facts.**

---

## 8. Refresh Research Cycle

Once the initial research cycle is complete, Franchisee Finder operates as a continuous
intelligence-maintenance system.

**High-confidence entities — quarterly.** Primary question: what, if anything, has changed since
the previous verification? Look for changes in unit counts, acquisitions, divestitures, new/
exited brands, development agreements, headquarters, ownership, investors, executive leadership,
geographic expansion/contraction, legal entities, major franchise relationships, relevant contact
information. Use change detection — don't unnecessarily reconstruct an already well-supported
profile every quarter.

**Low-confidence entities — monthly.** Objective: identify changes *and* improve confidence on
unresolved assertions by researching the specific evidence gaps responsible for lower confidence.
Once an entity/assertion crosses the configured high-confidence threshold, it moves to the
quarterly cycle. **Confidence thresholds must be configurable, not hard-coded.**

---

## 9. Change History

Never simply overwrite meaningful historical intelligence. Maintain history, e.g.:
- January 2027 — ABC Group / Taco Bell — 78 units
- April 2027 — ABC Group / Taco Bell — 84 units

Supporting evidence should explain *why* the value changed, so RBB can identify expansion,
contraction, acquisitions, transfers, and other strategically useful patterns.

---

## 10. User-Contributed Intelligence

Authorized RBB users can propose additions or corrections (incorrect unit count, missing
franchisee, changed executive, new headquarters, additional brand ownership, incorrect entity
relationship, newer public source).

User submissions do **not** automatically become canonical facts — they enter a review workflow.
A submission captures: submitting user, proposed change, affected organization/assertion,
supporting public source, source URL (where applicable), user comments, submission timestamp.

RBB/Codex validates the submission against public evidence before promotion into canonical
Franchisee Finder intelligence. A reviewer may approve/reject or request additional evidence.

---

## 11. Public-Information Requirement

Franchisee Finder contains intelligence derived from legitimate public business information
only: business addresses, headquarters, corporate telephone numbers, publicly listed business
email addresses, public professional profiles, corporate filings, public executive biographies,
franchise disclosure information, company websites, public announcements.

Avoid building a repository of private personal information simply because it can technically be
discovered. Registered addresses and operating headquarters must be represented separately when
they differ.

---

## 12. Team Portal — Franchisee Finder

New dedicated Team Portal feature, **Franchisee Finder**, with primary search: "Search
franchisee, brand, operator, executive, location, or company…" — searchable in both directions:

- **Brand → Franchisees** (e.g. "Show Taco Bell franchisees")
- **Franchisee → Portfolio** (e.g. "What brands does Flynn Group operate?")
- **Person → Organization** (e.g. "Which franchisee organization is John Smith associated with?")

### Filters
Brand, franchisee/operator, minimum units, maximum units, multi-brand operators only, geography,
state, region, headquarters, ownership type, public/private, confidence, last verified, parent
organization, named executive/contact. Architecture should allow additional filters as Franchisee
Finder becomes richer.

### Franchisee Profile (example shape)
```
ABC Restaurant Group
Headquarters: Dallas, TX — 95% confidence
Ownership: Privately held — 90%
Total identified restaurants: 127

Brand Portfolio
  Taco Bell — 84 — 91%
  KFC — 27 — 88%
  Arby's — 16 — 96%

Geographic Footprint
  Texas, Oklahoma, Arkansas, Louisiana

Leadership
  CEO — Jane Smith — 96%
  COO — Robert Jones — 92%
  Technology Leader — John Doe — 78%

Related Legal Entities
  ABC Taco LLC, ABC Chicken LLC, ABC Restaurants Texas LLC

Research Status
  Last researched: September 28, 2026
  Next scheduled review: December 2026
  Overall profile quality: High
```
Each relationship exposes its supporting evidence and confidence. UI must make clear that
confidence varies between individual assertions.

### Evidence Transparency
For important assertions, an evidence view containing: source, source date, access date,
supporting observation, confidence, confidence rationale, conflicting evidence, historical
values. RBB must never present inferred ownership relationships as confirmed facts without
exposing the distinction.

---

## 13. Relationship Graph

Model as a graph/relationship structure, not simple one-to-many ownership:

```
Person → Franchisee Organization → Parent Organization → Legal Entity → Brand → Restaurant Locations
```

A franchisee may have multiple legal entities, multiple brands, multiple geographic operating
companies, and multiple ownership relationships. A brand may have many franchisees.

---

## 14. Future Technology Intelligence (schema accommodation only, not Phase 1)

Design the schema so Franchisee Finder can eventually support technology-buying intelligence
without a redesign — categories: POS, Payments, KDS, Digital Menu Boards, Back Office, Inventory,
Workforce, Loyalty, Digital Ordering, Hardware. Per category, eventually represent: installed
technology, vendor, decision authority (franchisor mandated / franchisee selected / mixed
authority / unknown), evidence, confidence.

---

## 15. Relationship to Existing RBB Intelligence

Franchisee Finder should connect with existing restaurant brands, contacts, accounts,
opportunities, Account Background Briefs, marketplace intelligence, competitor intelligence,
research evidence, and Team Portal users.

Example: if RBB discovers one franchisee operates 72 Taco Bells, 31 KFCs, and 18 Wendy's, RBB
should recognize that as one 121-unit restaurant operating organization while preserving the
individual brands and legal entities involved. That intelligence should then be available to
other RBB workflows.

---

## 16. Recommended Development Phases

**Phase 1 — Franchise Ownership Foundation.** Franchisee artifact/schema; FDD/public-source
research workflow; organization records; legal entities; brand relationships; unit counts;
headquarters; geography; source/evidence model; confidence model; ChatGPT research packet; Codex
ingestion pipeline; basic Team Portal search. Should produce a useful standalone product.

**Phase 2 — Entity Resolution and People.** Sophisticated multi-brand operator resolution;
parent/subsidiary relationships; leadership; public business contact information; contact
matching; user-submitted corrections; review workflow; historical/change tracking.

**Phase 3 — Commercial Intelligence.** Technology relationships; technology decision authority;
installed vendors; franchisor-vs-franchisee technology control; RBB account/opportunity
integration; advanced prospecting queries.

---

## 17. Research Orchestration

```
ChatGPT Deep Research
  → discovers and documents public evidence
Structured Research Packet
  → preserves assertions, evidence, confidence, conflicts, provenance
Codex
  → validates, resolves entities, detects changes, processes the intelligence
Canonical Franchisee Finder Artifact
  → persistent RBB intelligence domain
RBB
  → makes the intelligence available to other systems and workflows
Team Portal / Individual RBB
  → query and presentation layer
Scheduled Research
  → monthly low-confidence / quarterly high-confidence refresh
User Contributions
  → review queue → public-source validation → canonical update
```
This division of responsibility must remain explicit in the implementation.

---

## 18. Definition of Success

An authorized user can ask RBB questions such as:
- Who are the largest franchisees of Brand X?
- Which franchisees operate more than 100 restaurants?
- Which operators own both Taco Bell and KFC restaurants?
- What does ABC Restaurant Group own? Who runs it? Where is it headquartered?
- How confident are we that ABC operates 84 Taco Bells? What evidence supports that unit count?
- What changed at ABC Restaurant Group since last quarter?
- Which franchisee records need additional research?

...and receive answers directly from canonical, sourced, confidence-rated RBB intelligence
rather than requiring a new internet search each time.

**Guiding Principle:** Franchisee Finder should know not only what RBB believes, but why RBB
believes it, how confident RBB is, when it was last checked, and what has changed. The objective
is not perfect certainty — it is an auditable, continuously improving map of the restaurant
franchise ownership ecosystem that can become actionable intelligence throughout RBB.

---

## 19. Open items before implementation (not in Todd's original request)

- Reconcile with `system/franchisee_hierarchy.json` (existing static Franchise Times extract) —
  decide whether it's a one-time seed import or discarded in favor of fresh FDD-sourced research.
- Define the concrete JSON schema / storage location (likely a new top-level `system/franchisee_finder/`
  domain, mirroring the `master_account_plans/` or `competitor_intelligence/` pattern) and register
  it in `SCHEMAS.md` and `CANONICAL_REGISTRY.yaml`.
- Define the new API operations (`system/api/server.py`) per `custom_gpt_instructions_compact_8k.md`
  conventions — e.g. `listFranchiseeOrganizations`, `getFranchiseeProfile`,
  `queryFranchiseesByBrand`, `addFranchiseeEvidence`, `submitFranchiseeCorrection`,
  `reviewFranchiseeSubmission` — each with explicit field-level validation per the review-first
  rule in `AGENTS.md`.
- Decide where the quarterly/monthly refresh cycle plugs into the existing intelligence-cycle
  scheduling (`INTELLIGENCE_CYCLES.md`) rather than creating a parallel scheduler.
