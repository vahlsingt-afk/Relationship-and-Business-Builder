# Relationship & Business Builder (RB) Canonical Workspace

Canonical workspace:

`/Users/toddvahlsing/Documents/Claude/Projects/Relationship & Business Builder`

Archived secondary workspace:

`/Users/toddvahlsing/Documents/Relationship Bridge.archived-2026-06-02`

As of 2026-06-02, Codex reconciled the secondary workspace into the canonical
workspace and archived the secondary directory. Claude and Codex should use only
the canonical workspace above for future RB work.

Deployment notes:

- Full internal/API deployment schema: `system/api/openapi.yaml`
- GPT-capped curated schema: `system/api/openapi_gpt.yaml`
- Ingest trigger prompt packet: `system/INGEST_TRIGGER_RULES.md`
- Deployment readiness check: `python3 system/scripts/pre_flight_check.py`

The full OpenAPI schema contains the direct `/ingest/...` and `/query/...`
routes from the secondary workspace. The GPT-capped schema remains constrained
to the Custom GPT 30-operation limit and is maintained separately by
`system/scripts/validate_openapi_gpt.py`.
