# Claude Handoff — Hunter Is RBB's Primary Research Agent

**Date:** 2026-10-02  
**Audience:** Claude instances working in the Relationship & Business Builder repository  
**Decision:** Hunter is the mandatory research layer for RBB public-source research.

## Executive instruction

Do not start or continue an independent “deep research cycle” using an older
free-form prompt, Markdown template, exporter-to-ChatGPT workflow, or manually
assembled JSON sidecar. Use Hunter.

Hunter governs research about:

- enterprise restaurant brands and operators;
- restaurant-technology companies, startups, suppliers, and competitors;
- leadership, ownership, funding, customers, deployments, technology stacks,
  replacements, strategy, operating health, and market changes;
- explicit gaps in RBB's current data;
- material net-new data points;
- what changed, which RBB records may need mutation, and which connected
  developments should be handed to the Chief of Staff for commentary.

The authoritative definition is:

- `system/research/HUNTER.md`
- `system/prompts/hunter_research_bot.md`
- `system/research/hunter_playbooks.json`
- `system/schemas/hunter_research_packet.schema.json`

If another document conflicts with these files, Hunter controls the research
method. Existing canonical mutation policies and human-authorization gates
still control persistence.

## Required workflow

### 1. Select the playbook

Choose the narrowest applicable playbook from
`system/research/hunter_playbooks.json`. Common routes:

| Research need | Hunter playbook |
|---|---|
| Enterprise restaurant baseline | `enterprise_account_profile` |
| Restaurant technology stack | `technology_stack_reconstruction` |
| Vendor, startup, or supplier profile | `vendor_startup_profile` |
| Customer or deployment claim | `customer_deployment_validation` |
| Technology replacement history | `technology_replacement_lifecycle` |
| Leadership decision map | `leadership_decision_map` |
| Ownership, funding, or M&A | `ownership_funding_ma` |
| Supplier and integration relationships | `supplier_ecosystem_map` |
| Changes since prior state | `change_monitor` |
| Cross-company industry pattern | `industry_change_scan` or a narrower thematic playbook |

Do not invent a new packet format because a playbook seems imperfect. Extend
the registered playbooks and payload registry deliberately if a real domain is
missing.

### 2. Prepare the job

Use Hunter's orchestrator:

```bash
python3 system/scripts/hunter_cycle.py prepare <playbook> \
  --target <real-rbb-target-key> \
  --output /tmp/hunter-job.json
```

For prioritized gap selection, use `--universe brands` or `--universe
competitors` and an appropriate `--limit`. Never invent a `company:<id>` or
`competitor:<slug>`.

The prepared job includes:

- the playbook and research depth;
- RBB's current known state;
- stable known-gap IDs;
- prior research context and unresolved conflicts;
- ranked public sources;
- the required payload schema;
- a hash-stamped before-state snapshot.

### 3. Execute through public-source research

Submit the prepared `directive` to the available ChatGPT Deep Research surface
with `system/prompts/hunter_research_bot.md`. Require exactly one JSON packet.

Hunter currently uses free public sources only. It must not sign into, trial,
purchase, scrape around, or rely on a paid source. If a material unresolved gap
appears to require paid data, return a paid-source recommendation documenting
the free sources and query families already exhausted. A recommendation is not
evidence or authorization.

ChatGPT Deep Research is not governed by Codex or Work usage windows. Check
those windows only if the cycle actually needs Codex or Work for recovery,
synthesis, or another substantive step. Never consume a usage reset credit
automatically.

### 4. Finalize and validate

```bash
python3 system/scripts/hunter_cycle.py finalize \
  /tmp/hunter-job.json /tmp/hunter-packet.json
```

Finalization defaults to a non-mutating dry run. It checks:

- Hunter envelope and playbook payload shape;
- exact source references and access consistency;
- gap outcomes and novelty rationale;
- evidence independence, dates, scope, and currentness;
- before-state versus claimed changes;
- mutation and CoS routing for material changes;
- the governed dispatch result.

Use `--confirm` only when the current calling function has actual authority to
persist registered safe writes and durable review queues. Never report a
canonical mutation merely because Hunter proposed one or a dry run recognized
a writer.

## What is now legacy compatibility only

The following may remain useful as queue inputs, historical records, rendered
views, or downstream import adapters. They are not independent research
methods:

- `system/prompts/chatgpt_deep_research_cycle.md`
- `system/templates/deep_research_intelligence_drop.md`
- `system/templates/deep_research_technology_lifecycle_drop.md`
- `system/scripts/deep_research_coverage.py`
- `system/scripts/deep_research_dataset_ingest.py`
- `system/scripts/vendor_extended_profile_ingest.py`
- historical packets in `system/inbox/chatgpt_intelligence_drop/`
- historical cycle state in `system/research/competitor_platform_cycle_2026-09-28.json`

Claude may read these for history, target prioritization, or compatibility. It
must not use them to bypass Hunter preparation, packet validation, change
comparison, or governed dispatch.

## Research truth rules Claude must preserve

- A logo wall is discovery evidence, not customer-deployment proof.
- Integration availability is not evidence that a restaurant uses it.
- Announcement, selection, pilot, rollout, and live deployment are different
  states.
- Franchisee evidence does not establish brand-wide deployment.
- Vendor-authored claims remain vendor claims until independently supported.
- Syndicated copies of one underlying source are one evidence chain.
- A current claim needs a dated basis; stale evidence must not be labeled
  current.
- Absence of public evidence is not evidence of absence.
- New data must explain why it is new relative to RBB's supplied state.
- Material changes must route to a mutation proposal, a CoS handoff, or both.
- CoS commentary must preserve source links, confidence, and inference labels;
  commentary is not itself a canonical fact.

## Scheduled cycles already migrated

The following Codex automations were updated on 2026-10-02 to use Hunter:

- RB Hunter Baseline Research
- RB Hunter Competitor Research
- RB Hunter Four-Day Research Burst
- RB Hunter Weekly Research
- RB Hunter Research Experiment — paused
- RB Hunter Research Governor — paused

Do not restore their previous `deep_research_coverage.py plan → Markdown plus
JSON sidecar` prompts.

## Verification before changing research infrastructure

Run:

```bash
python3 system/scripts/hunter_cycle_audit.py
python3 -m pytest \
  system/tests/test_hunter_contract.py \
  system/tests/test_hunter_runtime.py \
  system/tests/test_hunter_gap_manifest.py \
  system/tests/test_hunter_source_registry.py \
  system/tests/test_hunter_change_dispatch.py \
  system/tests/test_hunter_improvements.py \
  system/tests/test_hunter_cycle_audit.py -q
```

The adoption audit must remain valid. If Claude adds a new research automation,
prompt, or cycle entry point, add it to the audit and require Hunter's
prepare/finalize workflow.

## Bottom line

Hunter is not merely another prompt or optional research persona. Hunter is
RBB's research control plane: gap-aware intake, repeatable public-source method,
structured JSON evidence, change detection, governed mutation proposals, CoS
handoffs, validation, source learning, and auditable improvement. Calling
functions decide which cycle to run. They do not replace Hunter's method.
