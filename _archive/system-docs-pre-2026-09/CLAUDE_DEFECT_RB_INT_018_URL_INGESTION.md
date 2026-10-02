# RB-INT-018: User-Provided URLs Not Guaranteed to Be Fully Ingested

**Filed:** 2026-06-17
**Severity:** Medium-High
**Category:** Intelligence Pipeline / External Content Ingestion

---

## Problem Statement

When a user provides an intelligence-bearing URL (LinkedIn article, paywalled article,
dynamically rendered page, authenticated content), Relationship Bridge may fail to retrieve
the article body and instead produce inferred analysis -- without disclosing the failure.

This violates the expectation that all user-provided intelligence inputs are fully processed
and that the system proves what was scanned, what was learned, and where it encountered limits.

**Triggered by:** `https://www.linkedin.com/pulse/restaurant-software-reckoning-has-started-inc-tank-gtm-skbpe/`

---

## Platform Limitation vs. RB Defect Distinction

This is BOTH a platform limitation AND an RB defect:

- **Platform limitation (not RB's fault):** LinkedIn blocks article body retrieval behind
  authentication, dynamic JS rendering, anti-scraping protections, and session-specific access.
  GPT has no logged-in session, cookies, or browser context.

- **RB defect (fixable):** Silently degrading to inferred analysis without disclosing the
  retrieval failure. A user-provided source must never silently degrade into inferred intelligence.

---

## Expected Behavior

Every user-provided URL must generate a URL Ingestion Report before any analysis:

```
URL Ingestion Report
Source:              LinkedIn Article
URL:                 https://www.linkedin.com/pulse/...
Status:              Partial -- metadata only
Body Retrieved:      No
Reason:              Authentication / Dynamic Rendering Restriction (LinkedIn)
Alternate Sources:   3 found (mirrors, reposts, discussions, author's other posts)
Confidence Level:    Partial -- analysis based on metadata + alternate sources
Action Required:     Paste article text to complete full ingestion
```

Then continue the intelligence pipeline using all available evidence, with every claim
labeled [CONFIRMED] or [INFERRED] based on whether it came from retrieved body or inference.

---

## Required Retrieval Cascade (when direct URL fails)

1. Attempt direct retrieval
2. If failed: attempt alternate retrieval (Wayback Machine, Google cache, mirrors)
3. Search for reposts, quotes, author commentary, discussion threads
4. Retrieve author's other published content on same topic
5. Extract all available metadata (title, date, author, LinkedIn engagement signals)
6. Output URL Ingestion Report with status, reason, alternates found, confidence
7. Request article text ONLY as last resort -- after steps 1-5 are exhausted

---

## Implementation Note

The URL Ingestion Report is NOT the same as the Intelligence Receipt.
- Intelligence Receipt = confirms what was persisted to the knowledge base
- URL Ingestion Report = confirms what was retrieved (or not) from the source
Both should render when a URL is provided. URL Ingestion Report comes FIRST.

---

## Fix Target

Update `ingestContent` routing in compact instructions and 8k KB to:
1. Mandate URL Ingestion Report for every user-provided URL
2. Require retrieval cascade before requesting user text
3. Require [CONFIRMED] / [INFERRED] labeling when body retrieval failed
