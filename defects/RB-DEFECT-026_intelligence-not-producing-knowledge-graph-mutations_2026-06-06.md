# RB-DEFECT-026: Intelligence Ingestion Not Producing Knowledge Graph Mutations

**Date:** 2026-06-06  
**Severity:** High  
**Classification:** Intelligence Processing / Knowledge Graph / Daily Brief  
**Status:** In Remediation  

---

## Summary

The system discovers articles and summarizes them. It does not convert them into
durable knowledge graph mutations, entity profile updates, thesis validations, or
opportunity assessments. The article becomes the deliverable when the mutation should
be the deliverable.

---

## Root Cause

`intelligence_triage.py` classifies intelligence types and proposes mutations but
is explicitly read-only. No downstream engine converts triage proposals into actual
graph updates. The pipeline has:

```
Article → Triage → Brief (mentions article)
```

It should have:

```
Article → Triage → Mutation Engine → Graph Updates → Brief (reports mutations)
```

The mutation engine does not exist.

---

## Required Pipeline

### Stage 1 — Entity Extraction
From article text, extract:
- Brand/company mentions (Freddy's, PAR Technology, CrunchTime, etc.)
- Executive mentions with title and context (Sean Thompson, CTO)
- Technology/vendor relationships (Freddy's uses PAR for POS)
- Executive POVs/quotes ("AI should remain in the loop, not own the loop")

### Stage 2 — Entity Resolution
Match extracted entities against:
- `ecosystem_intelligence.json` (existing brand/vendor profiles)
- `baseline_index.json` (known contacts at named companies)
- `active_threads.yaml` (active opportunities)

### Stage 3 — Mutation Generation
Produce:
- Vendor-customer relationships (Freddy's confirmed PAR customer)
- Executive POV records (Sean Thompson AI philosophy)
- Entity profile expansions (Freddy's technology stack)
- Thesis validation signals (article supports "human-in-loop AI" thesis)

### Stage 4 — Confidence Assessment
- High confidence (named, explicit, primary source) → auto-apply
- Medium confidence (inferred, secondary) → propose with confirmation
- Low confidence (speculative) → log only

### Stage 5 — Brief Reporting
Report mutations, not articles:
```
Overnight Knowledge Mutations
Freddy's Frozen Custard — Trust: 91%
  New: confirmed PAR customer, CrunchTime customer
  Executive: Sean Thompson AI philosophy captured
  Thesis: human-in-loop-AI strengthened (+2)
  Mutations applied: 7
```

---

## Files Created

- `system/scripts/intelligence_mutation_engine.py` — new core engine
- `system/api/server.py` — POST /intelligence/mutate endpoint
- `system/scripts/daily_brief.py` — knowledge mutation brief section
- `system/api/openapi_gpt.yaml` — applyIntelligenceMutations operation
