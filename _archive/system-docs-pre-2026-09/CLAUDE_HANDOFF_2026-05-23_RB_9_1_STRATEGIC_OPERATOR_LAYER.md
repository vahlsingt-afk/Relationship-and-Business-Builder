# Claude Handoff — 2026-05-23 — RB 9.1 Strategic Operator Layer

## Sprint State

RB 9.1 is implemented and smoke-green through Priority 5.

Core framing accepted and implemented:

- Strategic Operator Intelligence is not a news enhancement.
- `market_signals.py` remains the ephemeral market/news/source lane.
- `strategic_operators.yaml` is the persistent, queryable operator entity lane.
- Primary scores are categorical (`low | medium | high | critical`), not numeric.
- Daily Brief now has a dedicated `strategic_operator_movements` canonical section separate from market/vendor/macro context.

## Files Added

- `system/strategic_operators.yaml`
- `system/schemas/strategic_operators.schema.json`
- `system/scoring/strategic_operator_rubric.md`
- `system/scripts/strategic_operators.py`
- `system/protocols/P-031_strategic_operator_intelligence.md`

## Files Modified

- `system/schemas/validate.py`
- `system/scripts/rb_core.py`
- `system/scripts/mutations.py`
- `system/api/server.py`
- `system/api/openapi.yaml`
- `system/api/openapi_gpt.yaml`
- `system/scripts/daily_brief.py`
- `system/scripts/refresh_all.py`
- `system/settings.json`
- `system/protocols/README.md`
- `system/protocols/index.json`
- `system/STATUS.md`

## What Shipped

### Priority 1 — Canonical State

Created `system/strategic_operators.yaml` with initial entries:

- Flynn Group
- Carrols Restaurant Group
- Sailormen
- Ghai Management
- Thrive Restaurant Group
- Sizzling Platter
- KBP Foods

Created JSON Schema and extended `validate.py` so strategic operators validate alongside baseline or independently with:

```bash
python3 system/schemas/validate.py --strategic-operators-only
```

### Priority 2 — Mutation Contract

Added operator mutation commands:

- `operator-add`
- `operator-update`
- `operator-record-movement`
- `operator-close`

They follow the P-009 snapshot-then-validate-then-rollback discipline.

Added FastAPI/GPT endpoint:

- `POST /strategic_operators/apply`
- GPT operation: `applyOperatorMutation`

GPT spec remains at the 30-operation cap by removing `getRecentTestTraces`.

### Priority 3 — Overlay + Rubric

Added `system/scripts/strategic_operators.py`.

It reads:

- strategic operator entity records
- market signal feeder rows
- baseline
- active threads

It produces:

- recent movements
- market feeder matches
- relationship proximity evidence
- mutual connections
- categorical scores
- recommended actions
- reconciliation prompts

The scoring rubric lives in `system/scoring/strategic_operator_rubric.md`.

### Priority 4 — Daily Brief Split

`daily_brief.py` now embeds:

- raw report key: `strategic_operators`
- canonical section: `strategic_operator_movements`

Rendering behavior:

- Strategic operator movements render from persistent operator rows only.
- Market/vendor/macro/news items render under `Market and restaurant-tech context`.
- Proximity-only watchlist rows do not become daily actions unless backed by movement, source-backed feeder match, direct relationship, active thread, or stronger evidence.
- If there are no operator movements, the brief prints a short health line rather than inventing commentary.

### Priority 5 — Protocol + Settings + Status

Added:

- `daily_briefing.strategic_operators` settings block
- P-031 protocol
- protocol index refresh
- STATUS update

`refresh_all.py` now runs `strategic_operators.py --cache --json` before `daily_brief.py`.

## Verification

Green checks run:

```bash
python3 system/scripts/strategic_operators.py --smoke
python3 system/schemas/validate.py --strategic-operators-only
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/validate_openapi_gpt.py
python3 system/scripts/refresh_all.py --date 2026-05-23
python3 system/scripts/daily_brief.py --date 2026-05-23 --cache --dry-run
```

Results:

- strategic operator smoke: 0 failures
- strategic operator schema: valid, 7 items
- daily brief smoke: 0 failures
- GPT OpenAPI: OK, 30 ops
- refresh_all: all caches refreshed
- full daily brief dry-run: succeeded

## Design Note for Next Sprint

The overlay currently resolves some `one_hop` proximity through portfolio-brand overlap, for example Taco Bell/Pizza Hut/Wendy's baseline contacts. That is useful candidate proximity, but it is intentionally not enough by itself to create an `act_today` Daily Brief item.

Next sprint should either:

- keep this as `one_hop` but treat it as candidate proximity in product language, or
- add a new category such as `one_hop_candidate` if the distinction matters in the schema.

## Recommended Next Sprint: RB 9.2 Source Instrumentation Completion

Do not reopen the strategic-operator architecture unless defects appear. The next unblocker is source instrumentation:

1. macOS Full Disk Access doc and first real P-019 run.
2. Personal Gmail raw connector capture contract.
3. Calendar feeds raw connector capture contract.
4. Ingest the real LinkedIn messaging export. Parser exists and is wired; no real export has been ingested yet.
5. End-to-end morning path test:
   source refresh -> daily brief -> publish -> email + push notification -> open GPT -> send "Show today's RB Daily Brief." -> correct brief.

## Deferred

- Composition layer / draft-in-voice.
- DRR eval framework.
- Protocol docs P-025 through P-030 for the execution-layer modules.
- Numeric strategic operator scores, pending calibration data.
