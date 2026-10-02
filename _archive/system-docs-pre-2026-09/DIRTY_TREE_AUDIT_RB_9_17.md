# Dirty Tree Audit — Sprint RB 9.17
Generated: 2026-05-27

> Note: No git repository is present at the system folder root. This audit is
> based on file classification rather than `git status`. All destructive
> recommendations require Todd confirmation before action.

---

## Summary

| Category | Count | Action |
|---|---|---|
| Sprint deliverables (keep + track) | ~20 CLAUDE_HANDOFF / CLAUDE_SPRINT .md files | Keep, already archived |
| Generated runtime artifacts | briefs (1,604), test_traces (many), inbox/, _snapshots/ | Keep, exclude from sprint scope |
| Duplicate script | `scripts/strategic_events (1).py` | **Delete** (stale macOS copy) |
| `__pycache__` / `.pyc` | 4 dirs, 87 files | Safe to delete; auto-regenerate |
| Prior dirty tree audit | `DIRTY_TREE_AUDIT_RB_9_9.md` | Superseded, archive or delete |
| Active user-authored docs | `SCHEMAS.md`, `ARCHITECTURE.md`, `STATUS.md`, etc. | Do not touch |
| Orphaned `_sessions/` session log | `_sessions/2026-05-16-2000.md` | Keep (user session record) |

---

## File-Level Detail

### DELETE — confirmed safe (stale duplicates / generated noise)

| File | Reason |
|---|---|
| `scripts/strategic_events (1).py` | macOS duplicate of `strategic_events.py`. One regex line behind current. Stale. |
| `DIRTY_TREE_AUDIT_RB_9_9.md` | Superseded by this audit. Archive or delete. |

**`__pycache__` dirs (4):**
- `api/__pycache__/`
- `scripts/__pycache__/`
- `tests/__pycache__/`
- `schemas/__pycache__/` (if present)

Safe to delete — Python regenerates on next run. Can add to `.gitignore` / equivalent exclusion.

---

### KEEP — runtime-generated artifacts (not sprint noise)

| Path | Contents | Note |
|---|---|---|
| `briefs/` | 1,604 daily brief files | Runtime output. Do not delete. |
| `test_traces/` | Operator audit traces | Runtime output. Do not delete. |
| `inbox/` | Live signal data (email, calendar, LinkedIn) | Runtime input. Do not delete. |
| `_snapshots/` | 206 snapshot files | Runtime history. Keep. |
| `_sessions/` | 3 session files | User session records. Keep. |
| `published/` | 1 published brief | Keep. |

---

### KEEP — user-authored docs (do not touch without Todd confirmation)

All `.md` files at root level: `SCHEMAS.md`, `ARCHITECTURE.md`, `MANIFEST.md`,
`STATUS.md`, `README.md`, `OPERATIONALIZATION.md`, `SECURITY_PRIVACY_ARCHITECTURE.md`,
`WHAT_PERSISTS.md`, `TEAM_MODE.md`, `RB_ONBOARDING_AND_ADOPTION_PLAN.md`,
`CANONICAL_RESPONSE_CONTRACT.md`, `heuristics.md`, and all `CLAUDE_HANDOFF_*` /
`CLAUDE_SPRINT_*` / `CODEX_HANDOFF_*` files.

---

### NEEDS TODD DECISION

| Item | Question |
|---|---|
| `CODEX_HANDOFF_2026-05-27_ECOSYSTEM_INTELLIGENCE_ALIGNMENT.md` | Is this handoff from a Codex session still actionable, or can it be archived? |
| `scripts/strategic_events (1).py` | Confirm deletion of stale duplicate? |
| `DIRTY_TREE_AUDIT_RB_9_9.md` | Archive or delete now that RB_9_17 audit supersedes it? |
| `cards/` (21 contact cards) | Confirm these are current and not duplicated anywhere. No action needed unless card schema changed. |

---

## Recommended Immediate Actions

```bash
# Delete stale duplicate (confirm first)
rm "system/scripts/strategic_events (1).py"

# Clear pycache
find system -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Archive old audit
mv system/DIRTY_TREE_AUDIT_RB_9_9.md system/_snapshots/DIRTY_TREE_AUDIT_RB_9_9_archived.md
```

---

## Sprint Deliverables Separation

Files changed or created in RB 9.17 sprint:
- `system/api/openapi.yaml` — 5 new endpoints added
- `system/api/openapi_gpt.yaml` — regenerated (30 ops, drift resolved)
- `system/scripts/validate_openapi_gpt.py` — GPT_OPERATIONS allowlist updated
- `system/tests/test_micro_graph_routing.py` — Python 3.9 compat fix
- `system/tests/test_api_ecosystem_graph.py` — proper fastapi skip guard
- `system/SCHEMAS.md` — updated for current behavior
- `system/DIRTY_TREE_AUDIT_RB_9_17.md` — this file
