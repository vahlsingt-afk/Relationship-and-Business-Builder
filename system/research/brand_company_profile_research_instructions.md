# Hunter Brand Company-Profile Research

Hunter is the only research method for brand company-profile cycles. The old
`export_research_gaps.py → free-form ChatGPT prompt →
deep_research_dataset_ingest.py` process is retired.

## Prepare

```bash
python3 system/scripts/hunter_cycle.py prepare enterprise_account_profile \
  --universe brands --limit 3 --output /tmp/hunter-brand-job.json
```

Use repeatable `--target company:<id>` when targets are already selected. For a
technology-focused pass, use `technology_stack_reconstruction`. Hunter loads
the live brand gap exporter, prioritizes enterprise targets, supplies known
state and stable gap IDs, and selects the registered payload schema.

## Research

Submit the job's `directive` to the signed-in ChatGPT Deep Research surface
with `system/prompts/hunter_research_bot.md`. Require exactly one Hunter JSON
packet. Do not ask for a standalone dataset or Markdown research narrative.

For `rb.brand_company_profile.v1`, `payload.records` is consumed by typed,
review-first importers. Each record must include the exact `target_key` and a
`record_type`; do not return one untyped aggregate company profile row. Use
only these record types: `company_identity`, `footprint_snapshot`,
`leadership_snapshot`, `franchise_disclosure`,
`financial_operating_snapshot`, `technology_relationship`, and
`technology_observation`. Keep each record to one type, attach its supporting
`source_ids` and `finding_ids`, and preserve the source's actual scope and
dates. If a fact does not fit one of these structures, retain it in packet
findings and payload context rather than forcing it into a different type.

Chat research is not governed by Codex or Work usage limits. Consult those
limits only if the cycle actually needs Codex/Work for recovery or synthesis.

## Finalize

```bash
python3 system/scripts/hunter_cycle.py finalize \
  /tmp/hunter-brand-job.json /tmp/hunter-brand-packet.json
```

The default is a non-mutating dry run. Review envelope and payload validation,
citation integrity, before-state comparison, change routing, and dispatcher
results. Use `--confirm` only when the calling function has explicit authority
to persist Hunter's registered safe writes and review queues.

`export_research_gaps.py` remains a Hunter input. The legacy
`deep_research_dataset_ingest.py` may consume a validated nested compatibility
payload, but it must never receive unvalidated research or define a separate
cycle. Entity mismatches, conflicts, overwrites, and unsupported field paths
remain review-first.
