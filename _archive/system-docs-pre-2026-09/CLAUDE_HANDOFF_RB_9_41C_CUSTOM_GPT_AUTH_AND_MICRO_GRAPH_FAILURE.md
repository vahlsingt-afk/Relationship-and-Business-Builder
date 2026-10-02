# Claude Handoff — RB 9.41C Custom GPT Auth / Micro Graph Failure

Date: 2026-05-31  
Canonical workspace path: `/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder`  
Do not use secondary workspace: `/Users/toddvahlsing/Documents/Relationship Bridge`

## Current live failure

Todd retested in the live Custom GPT after schema/instruction updates. It still bootstraps as degraded:

```text
[DEGRADED]
Micro Graphs: none mounted — entity queries fall back to Tier 2+
...
[RETRIEVAL FAILURE: McDonald’s Micro Graph unavailable — expected in active_knowledge_assets.]
...
The Daily Brief endpoint returned an API-key error, and the Micro Graph query failed before returning data.
```

The important new clue is the live GPT itself reports: **Daily Brief endpoint returned an API-key error**.

## Codex verified path alignment

Codex is working in:

```text
/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder
```

Key files modified/verified in that path:

```text
system/api/server.py
system/api/openapi_gpt.yaml
system/api/custom_gpt_live_audit_instructions_8k.md
system/scripts/validate_openapi_gpt.py
system/tests/test_daily_brief_action_payload_bootstrap.py
system/tests/test_custom_gpt_artifact_routing.py
```

As of the last Codex check:

```text
python3 -m pytest system/tests -q
1410 passed, 0 failing
```

## What Codex tried, in order

### 1. Verified local graph endpoints were healthy

Using FastAPI `TestClient`, local endpoints worked:

- `/graphs/micro/index` returned the McDonald’s micro graph.
- `/graphs/micro_summary?query=McDonald's` returned `status=found`.
- `/graphs/micro_summary?query=McDonald's&query_type=operator_count` returned:

```text
McDonald's U.S. micro graph currently contains 1347 operator/entity nodes with mapped stores,
14049 stores mapped to an operator/entity, 103 enterprise-scale operators/entities with 25+ stores,
807 mid-tier operators/entities with 5-24 stores, and 437 single-digit operators/entities with 1-4 stores.
```

Conclusion at this point: the Python graph layer was not the cause.

### 2. Repaired `openapi_gpt.yaml` server URL

Earlier `openapi_gpt.yaml`/`openapi.yaml` had pointed at localhost. Codex regenerated/updated schemas so the GPT-facing schema uses:

```yaml
servers:
  - url: https://rb-api.bridgepointops.org
```

Validated:

```text
python3 system/scripts/validate_openapi_gpt.py
validate_openapi_gpt: OK (30 ops)
```

### 3. Restored critical GPT operations under the 30-op cap

Current curated GPT schema includes retrieval-critical operations:

- `getDailyBrief`
- `getMicroGraphIndex`
- `getMicroGraphSummary`
- `getEcosystemGraphQuery`
- `listArtifacts`
- `getArtifact`
- `triageInput`

Schema remains at 30 operations.

### 4. Found `getDailyBrief` compact payload was stripping mount/spec fields

Local `/daily_brief` action payload initially returned:

```text
top active_knowledge_assets: None
top knowledge_retrieval_hierarchy: None
top intelligence_pipeline_spec: None
top session_bootstrap_spec: None
```

Root cause: `system/api/server.py` compacted `canonical_brief` for ChatGPT and omitted the top-level retrieval/bootstrap fields.

Patch added:

- `_compact_active_knowledge_asset()`
- `_compact_active_knowledge_assets()`
- duplicated these at top-level action payload:
  - `active_knowledge_assets`
  - `knowledge_retrieval_hierarchy`
  - `intelligence_pipeline_spec`
  - `session_bootstrap_spec`

Regression added:

```text
system/tests/test_daily_brief_action_payload_bootstrap.py
```

### 5. Restarted the API LaunchAgent process

Observed running processes:

```text
uvicorn system.api.server:app --host 127.0.0.1 --port 8765
cloudflared tunnel run rb-api
```

Killed uvicorn process and LaunchAgent restarted it.

After restart, public tunnel verified with API key:

```text
GET https://rb-api.bridgepointops.org/daily_brief
active_knowledge_assets length: 1
knowledge_retrieval_hierarchy: present
intelligence_pipeline_spec: present
session_bootstrap_spec: present
```

### 6. Added tiny top-level `session_bootstrap_status`

Because live GPT still missed `active_knowledge_assets`, Codex added a deliberately small top-level object in `system/api/server.py`:

```json
{
  "contract": "rb_session_bootstrap_status_v1",
  "micro_graphs_mounted_count": 1,
  "micro_graphs_mounted": [
    {
      "entity": "McDonald's US Operations Micro Ecosystem",
      "status": "MOUNTED",
      "retrieval_action": "getMicroGraphSummary",
      "activation_terms": ["mcdonalds", "mcdonald's", "nsn", "..."]
    }
  ],
  "retrieval_hierarchy_active": true,
  "intelligence_pipeline_active": true,
  "session_bootstrap_spec_active": true,
  "degraded": false
}
```

Public tunnel verified with API key:

```text
today 2026-05-31
source daily_brief_build
session_bootstrap_status.micro_graphs_mounted_count = 1
active_knowledge_assets length = 1
```

### 7. Updated compact GPT instructions

File:

```text
system/api/custom_gpt_live_audit_instructions_8k.md
```

Size:

```text
6892 bytes
```

Instruction change: read `session_bootstrap_status` first; if `micro_graphs_mounted_count > 0`, list mounted entities and do not print “none mounted.”

Also hardened rule: do not fall through to public/base-model estimates after mandatory Tier 1 retrieval failure.

This part improved behavior: GPT stopped giving public McDonald’s estimates.

### 8. Found public API behaves differently with/without API key

This is the current key fact.

Without `x-api-key`:

```bash
curl https://rb-api.bridgepointops.org/daily_brief
```

returns:

```json
{"detail":"invalid or missing x-api-key"}
```

With API key:

```bash
curl -H 'x-api-key: <RB_API_KEY>' https://rb-api.bridgepointops.org/daily_brief
```

returns:

```text
session_bootstrap_status.micro_graphs_mounted_count = 1
active_knowledge_assets length = 1
source = daily_brief_build
```

Therefore, RB public API and tunnel are alive. The live GPT is apparently calling without a valid `x-api-key`.

### 9. Repaired OpenAPI auth declaration

Previous schema exposed `x-api-key` as an optional model-filled header parameter on every operation. Codex changed generation in:

```text
system/scripts/validate_openapi_gpt.py
```

Now `openapi_gpt.yaml` declares:

```yaml
components:
  securitySchemes:
    ApiKeyAuth:
      type: apiKey
      in: header
      name: x-api-key
security:
  - ApiKeyAuth: []
```

And removes `x-api-key` from operation parameter lists.

Regression added to:

```text
system/tests/test_custom_gpt_artifact_routing.py
```

Validated:

```text
python3 system/scripts/validate_openapi_gpt.py --write
validate_openapi_gpt: OK (30 ops)
```

Todd pasted the regenerated schema and configured the Action, but live GPT still reports API-key error.

## Current diagnosis

The RB code path is healthy:

- canonical workspace is correct
- public tunnel is alive
- API key-authenticated calls work
- McDonald’s graph is mounted
- micro summary operator count works
- tests are green

The remaining failure is almost certainly in the **Custom GPT Action configuration**, not the Python code:

1. The GPT action is not sending the API key header.
2. The GPT Builder may not have saved the auth value after schema replacement.
3. The action may be using an older action/schema instance.
4. The schema may have been pasted but the Authentication panel still set to “None.”
5. The header name may not be exactly `x-api-key`.
6. There may be multiple Actions in the GPT, and the live GPT is calling the wrong/stale one.

## Recommended Claude next steps

### A. Confirm exact action auth in GPT Builder

In Custom GPT Builder > Configure > Actions:

- Authentication: API Key
- Auth type: Custom
- Header name: `x-api-key`
- Value: current RB API key
- Save the GPT after setting auth

Then use the Builder “Test” panel if available to call:

```text
getDailyBrief
```

Expected result must include:

```json
"session_bootstrap_status": {
  "micro_graphs_mounted_count": 1,
  "degraded": false
}
```

If the Builder test shows `invalid or missing x-api-key`, stop. This is definitely action auth configuration.

### B. If GPT Builder auth cannot be made reliable, consider a temporary diagnostic endpoint

For diagnosis only, add a read-only unauthenticated endpoint such as:

```text
GET /public/bootstrap_status
```

that returns only:

- date
- `session_bootstrap_status`
- no private relationship data

If the GPT can read that but not `/daily_brief`, the issue is confirmed as auth header delivery.

Do not expose full daily brief or graph details unauthenticated.

### C. Avoid more retrieval-rule code until auth is proven

The live GPT is now obeying failure posture correctly. More prompt scaffolding will not fix a missing API key header.

## Clipboard files last provided to Todd

Codex copied these during the session:

```text
system/api/openapi_gpt.yaml
system/api/custom_gpt_live_audit_instructions_8k.md
```

Make sure Claude uses these files from the canonical workspace path above, not the secondary workspace.
