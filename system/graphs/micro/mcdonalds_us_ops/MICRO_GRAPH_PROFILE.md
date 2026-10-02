# McDonald's US Operations Micro Graph Profile

**Graph ID:** `micro_ecosystem:mcdonalds_us_ops`  
**Graph slug:** `mcdonalds_us_ops`  
**Status:** partial, indexed, locally queryable  
**Source workbook:** `NSN Lookup 2026-05 MAY.xlsx`  
**Source SHA-256:** `9ca8333c54a17c1e896ad67316bb7aca797c0a9d874132ba2b345cd14b35da0b`

## Canonical Location

This is the standalone McDonald's micro ecosystem graph artifact.

- Registry: `system/graphs/index.json`
- Compact retrieval index: `system/graphs/micro/mcdonalds_us_ops/index.json`
- Full graph: `system/graphs/micro/mcdonalds_us_ops/graph.json`
- Source metadata: `system/graphs/micro/mcdonalds_us_ops/sources.json`
- Activation terms: `system/graphs/micro/mcdonalds_us_ops/activation.json`
- Extractor: `system/scripts/micro_graph_mcdonalds.py`
- Query/index helper: `system/scripts/micro_graph_query.py`

## What This Artifact Is

This is Todd's private McDonald's operational topology graph, derived from the NSN / StoreTech workbook. It is not generic restaurant industry intelligence and it is not Todd's personal relationship graph.

It should activate for queries about:

- McDonald's NSNs or store/site lookup
- StoreTech
- McDonald's field offices
- coops / co-ops
- markets
- OTM / STIM / RFM / FBP / OTP coverage
- operator/entity store distribution
- operational accountability or deployment topology

## Current Indexed Counts

- Nodes: 19,694
- Edges: 127,385
- Stores: 14,416
- Operator/entity nodes with mapped stores: 1,347
- Stores mapped to an operator/entity: 14,049
- Enterprise-scale operator/entity nodes, 25+ stores: 103
- Mid-tier operator/entity nodes, 5-24 stores: 807
- Single-digit operator/entity nodes, 1-4 stores: 437
- Field offices with mapped stores: 10
- Markets with mapped stores: 46
- Coops with mapped stores: 49

## Canonical Summary Answer

McDonald's U.S. micro graph currently contains 1,347 operator/entity nodes with mapped stores, 14,049 stores mapped to an operator/entity, 103 enterprise-scale operators/entities with 25+ stores, 807 mid-tier operators/entities with 5-24 stores, and 437 single-digit operators/entities with 1-4 stores.

## Guardrails

- This data is tenant-owned private intelligence for Todd's RB instance only.
- Do not include this graph, the workbook, or derived indexes as defaults for other users.
- Operational people in this graph are not automatically RB relationship contacts.
- Phone numbers were intentionally omitted from person nodes.
- Use `index.json` or `getMicroGraphSummary` for retrieval; load `graph.json` only for detailed traversal.
- For McDonald's operational/topology questions, RB must call `getMicroGraphSummary` before answering. If that action is unavailable in a live Custom GPT session, the correct answer is to report the unavailable action, not to answer from general industry knowledge.
