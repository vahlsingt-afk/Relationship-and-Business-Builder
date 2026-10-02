# Hunter Vendor and Startup Research

Hunter is the only research method for restaurant-technology vendors,
startups, suppliers, competitors, products, customers, news, and trends. The
old vendor-specific free-form ChatGPT cycle is retired.

## Prepare

```bash
python3 system/scripts/hunter_cycle.py prepare vendor_startup_profile \
  --universe competitors --limit 3 --output /tmp/hunter-vendor-job.json
```

Use `customer_deployment_validation` for a disputed customer/deployment claim,
`competitive_positioning` for a deeper competitive profile, and repeatable
`--target competitor:<slug>` for explicit targets. Hunter loads vendor and
competitor gap exporters into one gap manifest.

## Research

Submit the job's `directive` to ChatGPT Deep Research with
`system/prompts/hunter_research_bot.md`. Require exactly one Hunter JSON packet
and the registered playbook payload. Preserve vendor claims as vendor claims;
do not promote logos, integrations, announcements, pilots, or franchisee use
into enterprise deployments.

Chat research is not governed by Codex or Work usage limits. Consult those
limits only if the cycle actually needs Codex/Work.

## Finalize

```bash
python3 system/scripts/hunter_cycle.py finalize \
  /tmp/hunter-vendor-job.json /tmp/hunter-vendor-packet.json
```

Finalization defaults to dry run and checks schema, payload, citations,
before-state deltas, material-change routing, and governed dispatch. Use
`--confirm` only with explicit write authority.

`export_vendor_extended_profile_gaps.py` remains a Hunter input.
`vendor_extended_profile_ingest.py` remains a compatibility writer for a
validated nested payload; it does not define an independent cycle and must not
bypass Hunter's review-first gate.
