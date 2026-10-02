#!/bin/bash
# RB cleanup commit script — run from the project root in your terminal.
# This script: removes the git lock, deletes the bad filename, and commits
# sprint 9.6-9.13 and 9.14 work in two scoped commits.
#
# Usage:
#   cd ~/Documents/Claude/Projects/Relationship\ Builder
#   bash system/scripts/cleanup_commit.sh

set -e
cd "$(dirname "$0")/../.."

echo "=== RB Cleanup Commit ==="

# 1. Remove stale git index lock (left by a prior crashed git process)
if [ -f .git/index.lock ]; then
  echo "Removing stale .git/index.lock..."
  rm -f .git/index.lock && echo "Lock removed." || echo "Warning: could not remove lock — if git add fails, run: rm -f .git/index.lock"
fi

# 2. Remove bad filename (space in name)
BAD_FILE="system/scripts/strategic_events (1).py"
if [ -f "$BAD_FILE" ]; then
  echo "Removing bad filename: $BAD_FILE"
  rm -f "$BAD_FILE"
fi

# -----------------------------------------------------------------------
# COMMIT 1 — RB 9.6-9.13: pipeline hardening, security, and privacy
# -----------------------------------------------------------------------
echo ""
echo "--- Staging commit 1: RB 9.6-9.13 ---"

# All tracked modified files
git add \
  defects/RB-DEFECT-001_api-availability-regression_2026-05-24.md \
  system/01_RB_TENETS.md \
  system/ARCHITECTURE.md \
  system/BOOTSTRAP.md \
  system/CANONICAL_RESPONSE_CONTRACT.md \
  system/CLAUDE_DEVELOPMENT_MAP.md \
  system/MANIFEST.md \
  system/RB_9_0_STRATEGIC_DIRECTION_CHIEF_OF_STAFF_PLATFORM.md \
  system/RB_ONBOARDING_AND_ADOPTION_PLAN.md \
  system/SCHEMAS.md \
  system/STATUS.md \
  system/WHAT_PERSISTS.md \
  system/active_threads.yaml \
  system/api/custom_gpt_instructions_8k.md \
  system/api/custom_gpt_prompt.md \
  system/api/openapi.yaml \
  system/api/openapi_gpt.yaml \
  system/api/server.py \
  system/automation/com.relationshipbuilder.morning-pipeline.plist.template \
  system/heuristics.md \
  system/inbox/accounts.yaml \
  system/intro_brokers.md \
  system/loop_ledger.md \
  system/protocols/P-001_daily_brief_regen.md \
  system/protocols/P-002_linkedin_ingest.md \
  system/schemas/validate.py \
  system/scripts/daily_brief.py \
  system/scripts/fetch_google.py \
  system/scripts/intro_engine.py \
  system/scripts/linkedin_own_engagement.py \
  system/scripts/market_signals.py \
  system/scripts/morning_pipeline.py \
  system/scripts/morning_pipeline_install.py \
  system/scripts/publish.py \
  system/scripts/rb_core.py \
  system/scripts/refresh_all.py \
  system/scripts/refresh_sources.py \
  system/scripts/ri_intake.py \
  system/scripts/task_delivery_check.py \
  system/scripts/tunnel_health_check.py \
  system/scripts/update_tunnel_url.py \
  system/scripts/validate_openapi_gpt.py \
  system/settings.json

# New files from 9.10-9.13 sprints
git add \
  system/CLAUDE_HANDOFF_RB_9_12_OPPORTUNITY_SENSING_PROFILE_ARCHITECTURE.md \
  system/CLAUDE_SPRINT_RB_9_10_DURABLE_SOURCES_AND_INTRO_ENGINE.md \
  system/CLAUDE_SPRINT_RB_9_11_CONTINUOUS_STRATEGIC_AWARENESS.md \
  system/CLAUDE_SPRINT_RB_9_12_OPPORTUNITY_SENSING_AND_PROFILE_ARCHITECTURE.md \
  system/CLAUDE_SPRINT_RB_9_13_SECURITY_PRIVACY_AGENTIC_ARCHITECTURE.md \
  system/PROFILE_NAMING_CONVENTION.md \
  system/RB_9_RESTAURANT_REALITY_INTELLIGENCE_MODEL.md \
  system/SECURITY_PRIVACY_ARCHITECTURE.md \
  system/onboarding/ \
  system/protocols/P-037_strategic_event_convergence.md \
  system/protocols/P-038_privacy_security_ingestion.md \
  system/scripts/apple_access_check.py \
  system/scripts/audit_log.py \
  system/scripts/durable_tunnel_install.py \
  system/scripts/linkedin_freshness_bridge.py \
  system/scripts/market_signals_ri.py \
  system/scripts/market_source_feeds.py \
  system/scripts/opportunity_sensing.py \
  system/scripts/passive_email_intelligence.py \
  system/scripts/privacy_guard.py \
  system/scripts/retention_policy.py \
  system/scripts/source_health_report.py \
  system/scripts/strategic_events.py \
  system/scripts/user_profile.py \
  system/vendor/

# Tests from pre-9.14 sprints
git add \
  system/tests/test_intro_engine.py \
  system/tests/test_linkedin_freshness_bridge.py \
  system/tests/test_market_source_feeds.py \
  system/tests/test_privacy_guard.py \
  system/tests/test_prompt_injection_resistance.py \
  system/tests/test_retention_policy.py \
  system/tests/test_strategic_events.py \
  system/tests/test_strategic_events_9_11.py \
  system/tests/test_user_profile.py

git commit -m "RB 9.6-9.13: pipeline hardening, security, privacy, and agentic architecture

Accumulated sprint output from RB 9.6 through 9.13:
- Daily brief, morning pipeline, and source refresh hardening (9.6-9.8)
- LinkedIn engagement, intro engine, and opportunity sensing (9.8-9.12)
- Strategic events and market signals layer (9.11)
- Security/privacy architecture: privacy_guard, audit_log, retention_policy (9.13)
- P-038 privacy ingestion protocol and SECURITY_PRIVACY_ARCHITECTURE.md (9.13)
- API (OpenAPI, server.py), rb_core, ri_intake updates across sprints
- Tests: privacy guard (35), injection resistance (31), retention policy (46), others"

echo "Commit 1 done."

# -----------------------------------------------------------------------
# COMMIT 2 — RB 9.14: ecosystem intelligence
# -----------------------------------------------------------------------
echo ""
echo "--- Staging commit 2: RB 9.14 ---"

git add \
  system/CLAUDE_HANDOFF_RB_9_14_ECOSYSTEM_VENDOR_EVIDENCE_COMPLETE.md \
  system/CLAUDE_SPRINT_RB_9_14_ECOSYSTEM_VENDOR_EVIDENCE_AND_VERIFICATION.md \
  system/CLAUDE_SPRINT_RB_9_14_KICKOFF.md \
  system/CODEX_HANDOFF_2026-05-27_ECOSYSTEM_INTELLIGENCE_ALIGNMENT.md \
  system/design/ \
  system/domain_packs/ \
  system/ecosystem_intelligence.json \
  system/inbox/ecosystem/ \
  system/schemas/ecosystem_intelligence.schema.json \
  system/scripts/ecosystem_intelligence.py \
  system/tests/test_ecosystem_intelligence.py

git commit -m "RB 9.14: ecosystem intelligence — vendor evidence and verification

Persistent restaurant ecosystem graph seeded with Technomic Top 1500 data
and first POS vendor evidence pass for top 10 priority brands.

Graph state at sprint close:
- 1,586 entities (1,579 brands + 7 vendors)
- 12 POS relationships across all 10 priority brands
- Evidence posture: substantiated 2, partially_substantiated 9, provisional 1
- Vendor roles: system_of_record_pos 8, legacy_incumbent 3, approved_hardware_vendor 1
- 14 sources, 2 source files (Technomic 2024 + 2025)

New capabilities:
- query-brand: full brand profile with vendor relationships, signals, assessments
- query-brands: filter by segment / rank / sales threshold
- promote-posture: forward-only posture promotion with corroboration requirement
- 10-class source quality model (primary_operator_statement → unsourced_spreadsheet)
- POS vendor_role field: structurally prevents NCR/NewPOS collapse
- Audit log integration: source_accessed + item_persisted on every live ingest

Tests: 14 total (9 new this sprint)"

echo "Commit 2 done."

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
echo ""
echo "=== Done ==="
git log --oneline -4
echo ""
echo "Remaining uncommitted (intentional — runtime/personal data):"
git status --short | grep "??" | grep -v "^?? system/tests/__pycache__"
