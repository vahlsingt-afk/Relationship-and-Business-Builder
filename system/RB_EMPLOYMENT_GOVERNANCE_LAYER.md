# RB Feature Brief — Employment Governance & Organizational Context Engine

**Status:** Phase 1 built and tested (2026-07-07). Phase 2/3 remain proposed.
**Source:** Todd's "Employment Governance & Organizational Context Engine" feature request, 2026-07-06 (Todd's first day at Global Payments — see `personal_operating_system.yaml`).
**Related:** `CLAUDE_ARCHITECTURE_2026-07-06_ENTERPRISE_WORKSPACE_AND_EMPLOYMENT_GOVERNANCE.md` is a broader, separately-authored vision (Enterprise Workspace connectors, Data Classification Engine, Enterprise Memory separation) covering the same day. This document is the concrete implementation of that doc's "Employment Governance Engine ingesting policy documents" step — its own Status Notes call that out as the right starting point before any connector work. The connector/classification/memory-separation scope in that doc is NOT covered here and remains unscoped.

## Objective

RB currently models who Todd is (voice, values, goals — `00_TODD_PROFILE.md`, `personal_operating_system.yaml`) and who he knows (`baseline_index.json`, `cards/`, `circles/`). It has no model of the constraints his employer places on him: what he can say publicly, what requires approval, what's confidential, what could create a conflict of interest. A recommendation engine that doesn't know these boundaries can be strategically correct and still be a policy violation. This brief adds that missing model as a first-class input to every RB recommendation, not a bolt-on filter.

## Naming note

The existing brief architecture already uses "Layer 1/2/3" for document structure (Collection Report / Intelligence Brief / Daily Brief — `CLAUDE_DEFECT_RB_044_THREE_LAYER_BRIEFING_ARCHITECTURE.md`). To avoid collision, this feature is called the **Employment Governance Layer (EGL)** throughout, not "Layer 4."

## Core principle

RB optimizes performance *within* constraints, not around them. Employer policy is a first-class input, evaluated the same way DRR, cooldowns, and confidence floors are evaluated in `RB_Action_Recommendation_Policy.docx` — deterministically, auditably, with restraint as a legitimate and expected output.

## Precedent already in the codebase

`RB_Action_Recommendation_Policy.docx` already establishes the pattern this needs:
- Policy is code, not prose — rules are versioned and testable; the LLM never makes the policy decision, only generates rationale/language after policy has decided.
- Restraint is first-class — `no_action_recommended` is a normal, frequent output, not a failure mode.
- Every decision traces to a specific rule firing on specific inputs (auditability).

EGL reuses this pattern for compliance instead of relationship cooldowns. Where the action-recommendation engine asks "should RB suggest outreach right now?", EGL asks "is this specific piece of content/action clear to go, given who employs Todd and what they've agreed to?"

## Scope boundary (important)

EGL evaluates **Todd's outbound actions and content** against **his own employer's policies**. It does not attempt to model other people's employers, and it does not make legal judgments — it flags risk and proposes compliant rewrites; a human (Todd) makes the final call. Confidence in an auto-extracted "restriction" should never be higher than confidence in a hand-verified one.

---

## 1. Employer Profile

New structured object, one per employer, one active at a time.

**Storage:** `system/employers/<employer_id>/profile.yaml` (new directory). `employer_id` is a slug, e.g. `global-payments`.

```yaml
employer_id: global-payments
company: "Global Payments Inc. / Genius"
role:
  title: "Account Executive"
  responsibilities: "Enterprise restaurant vertical, software-funded payments"
  products: ["Genius POS", "..."]
  customers: ["enterprise QSR operators"]
  industry: "restaurant technology / payments"
start_date: "2026-07-06"
end_date: null          # set on offboarding; profile archived, not deleted
manager: null
status: active           # active | archived
travel_expectations: null
compliance_requirements: []
security_classification: null
internal_systems: []
policy_refs: []           # list of policy_id from Section 2, resolved at load time
```

`status: active` — exactly one employer profile has this at a time. Changing employers sets the old one to `archived` (with `end_date`) and creates/activates the new one — profile history is preserved, never deleted (mirrors the "ask git" no-delete convention already used for `MANIFEST.md`).

This slots as a new domain-adjacent file, read the same way `personal_operating_system.yaml` is read into brief context — but consumed by the compliance engine (Section 3), not rendered directly in the brief.

## 2. Policy Knowledge Base

**Storage:** `system/employers/<employer_id>/policies/<policy_id>.yaml` — one file per source policy document.

Each policy is parsed into structured fields, not kept as a raw PDF/docx blob:

```yaml
policy_id: social-media-policy
title: "Global Payments Social Media Policy"
source_document: "Global_Payments_Social_Media_Policy_v3.pdf"   # kept in system/employers/<id>/source_docs/, never in git if it contains employer-confidential text — see Section 7
version: "3"
effective_date: "2025-11-01"
superseded_by: null
extracted_by: manual        # manual | assisted (LLM-drafted, human-approved) — see Section 6
last_reviewed: "2026-07-06"

permissions:
  - "Personal LinkedIn use is permitted."
  - "Sharing publicly-available company news/press releases is permitted."

restrictions:
  - id: no-speaking-for-company
    text: "Employees may not speak on behalf of the company without authorization."
  - id: no-confidential-disclosure
    text: "Roadmap, pricing, and customer-specific information may not be shared externally."

required_processes:
  - trigger: "public speaking engagement"
    process: "Manager + Comms approval required"
  - trigger: "press/media inquiry"
    process: "Route to Corporate Comms, do not respond directly"

escalation_rules:
  - condition: "customer entertainment > $500"
    action: "manager approval required"
```

Same shape for `code-of-conduct`, `expense-policy`, `travel-policy`, `insider-trading-policy`, `confidentiality-agreement`, `ip-assignment`, `non-solicitation`, etc. — one file per document, same four sections (`permissions`, `restrictions`, `required_processes`, `escalation_rules`), so the compliance engine (Section 3) can iterate policies generically instead of special-casing each one.

**Document lifecycle:** superseding a policy sets `superseded_by` on the old file and creates a new one with a fresh `version`/`effective_date` — old versions stay on disk for historical review (e.g. "was this LinkedIn post compliant *at the time*, under v2 of the policy?").

## 3. Compliance Reasoning Engine

New script: `system/scripts/compliance_engine.py`, following the same deterministic-rule-engine shape as `RB_Action_Recommendation_Policy.docx` (no LLM policy decisions).

**Input bundle:**
```
ComplianceRequest {
  content: string                 # the draft LinkedIn post / email / message being evaluated
  content_type: enum               # linkedin_post | email | public_comment | presentation | ...
  audience_category: enum | null  # customer | partner | competitor | internal | board | investor | media | analyst | government | personal — see Section 5
  employer_id: string              # resolved from active employer profile
}
```

**Evaluation:** for each policy file under the active employer's `policies/`, check `restrictions[]` against content (keyword/entity match against known confidential terms — roadmap codenames, unannounced customers, pricing — sourced from the employer profile's `products`/`customers` plus an explicit `confidential_terms` list per policy), and check `escalation_rules[]` against any quantifiable fields in the request (dollar amounts, event types).

**Output:**
```
ComplianceVerdict {
  overall_risk: LOW | MEDIUM | HIGH
  per_policy: [
    { policy_id, status: clear | flagged, reason, matched_restriction_id }
  ]
  recommendation: allow | rewrite_required | escalate_required
  rewrite_guidance: string | null   # e.g. "remove reference to Q3 roadmap item X; rephrase as publicly available info"
}
```

This mirrors the dashboard shape from Todd's brief ("✓ Confidentiality / ⚠ Social Media / Overall Risk: LOW") directly — that's the CLI/API output format, not a new UI concept.

**Where it plugs in (no new surface needed):**
- LinkedIn ingest/publish pipeline (`system/scripts/linkedin_ingest.py` per `project_linkedin_ingest_pipeline.md` memory) — the highest-exposure existing pipeline; content evaluated before any "post this" recommendation is surfaced.
- Draft message generation in the action-recommendation engine — any `share_article` or drafted outreach touching customer/competitor/media contacts runs through `compliance_engine.py` before the draft is shown.
- New API op `POST /compliance/check` (only if/when the Custom GPT needs to invoke it directly — not needed for the two pipeline hooks above).

## 4. Content Firewall

Not a separate system — this *is* the Compliance Reasoning Engine (Section 3) applied at the specific chokepoints where Todd-authored content reaches an external audience: LinkedIn posts, drafted emails to customers/press/analysts, presentation/talk material if RB is ever asked to draft it. Internal-only content (notes to self, private journaling via captures) does not need to pass through it — scope this to *outbound, externally-visible* content only, or the engine will generate constant false-positive friction on content that was never at risk.

## 5. Relationship-Category Compliance Tagging

Todd's brief calls for distinguishing customer / partner / competitor / internal / board / investor / media / analyst / government relationships, since each carries different compliance weight (e.g. a LinkedIn comment aimed at a competitor's employee carries different risk than one aimed at a personal friend).

This does **not** reuse `relationships[].relationship_classification` in `SCHEMAS.md` (that model scores *vendor deployment strength* — pilot vs. strategic_platform — a different axis entirely). Instead, add a new optional field to the existing person schema in `baseline_index.json`:

```json
"compliance_category": "customer"
```

Values: `customer | partner | competitor | internal | board | investor | media | analyst | government | personal | unknown`. Default `unknown` — heuristically inferred from `current_company` against the active employer profile's `customers`/known-partner/known-competitor lists where possible, otherwise left for manual tagging (same "advisory, never guessed past evidence" discipline already used for `relationship_classification`). `compliance_category` feeds `audience_category` in the `ComplianceRequest` bundle above when RB drafts something addressed to a specific person.

## 6. On automated policy-document parsing

Todd's brief describes ingesting full policy PDFs and auto-extracting permissions/restrictions. That's the single highest-risk part of this system: a false negative here (a restriction that exists in the real document but wasn't extracted) produces exactly the outcome this whole feature exists to prevent — RB recommending something non-compliant with full confidence.

Recommendation: do not build automated extraction in the first phase. `extracted_by: manual` (Todd or an LLM-assisted first draft that Todd reviews line-by-line against the source document before `extracted_by: assisted` is marked reviewed) for every policy file, indefinitely, unless/until there's a track record showing assisted extraction reliably catches what manual review catches. This is a one-time cost per policy document (there are maybe 8-10 for one employer) versus an ongoing silent-failure risk.

## 7. Handling source documents

Policy source documents (the actual PDFs/docx from HR/Legal) likely contain employer-confidential material themselves. `system/employers/<id>/source_docs/` should be **git-ignored** (add to `.gitignore`), with only the extracted structured YAML (Section 2) committed. This mirrors why `RB_Action_Recommendation_Policy.docx` lives as a binary artifact already — but policy *source* docs are more sensitive than an RB-internal design doc, and shouldn't round-trip through git history at all.

## 8. Multi-employer history

`system/employers/<employer_id>/` per employer, `status: active|archived` on each `profile.yaml`, is already multi-employer-native — no separate registry file needed. Archived employers keep their full profile + policy history on disk for historical context (e.g. answering "what were the rules when I worked at PAR" without needing them live).

**Revised during Phase 1 build:** the original invariant ("exactly one active profile") assumed strictly sequential employment and broke the first time it met reality — Todd has a primary employer (Global Payments) plus a winding-down advisory engagement (BridgePoint Ops) active concurrently. Each profile now carries `relationship_type: primary_employer | advisory_engagement`. The invariant is "exactly one `status: active` profile with `relationship_type: primary_employer`" — advisory engagements can coexist without violating it.

---

## Phasing

**Phase 1 — built 2026-07-07:**
- `system/employers/global-payments/profile.yaml` + `system/employers/bridgepoint-ops/profile.yaml`, hand-encoded from the actual documents found in `~/Downloads` (2026 Team Member Handbook, Code of Conduct and Ethics 030525, Confidentiality/Assignment/Non-Solicitation Agreement Rev. 7/2025). Source PDFs copied to `system/employers/global-payments/source_docs/` (git-ignored per Section 7).
- 4 policy files under `system/employers/global-payments/policies/`: `social-media-policy.yaml`, `code-of-conduct.yaml`, `confidentiality-assignment-non-solicitation.yaml`, and a deliberately incomplete `travel-expense-policy.yaml` stub (the Handbook references a separate Corporate Travel Policy/expense policy that hasn't been provided — no dollar-threshold escalation rules were invented).
- `system/scripts/compliance_engine.py` — deterministic keyword/phrase detection against the encoded restrictions, `ComplianceVerdict` (low/medium/high, allow/warn/block), and `format_verdict_dashboard()` rendering the ✓/⚠ dashboard format from this doc's original mockup. 17 tests in `system/tests/test_compliance_engine.py`.
- **Actual integration point (revised from the original plan):** there is no live "draft a LinkedIn post for Todd to publish" pipeline in this codebase — `linkedin_ingest.py` processes *inbound* network-export data, not outbound content. The real hook is `privacy_guard.py`'s existing `gate_action()`, which already declared `send_linkedin_message`/`send_email` as gated action types (unused by any live caller yet). `gate_action()` now accepts optional `content`/`content_type` kwargs; for `CONTENT_BEARING_ACTION_TYPES`, a HIGH verdict blocks unconditionally (confirmation cannot override a compliance block), a MEDIUM verdict attaches a warning but doesn't block. 4 new tests in `test_privacy_guard.py` (RB-ACTIONGATE-008 through 011).
- `relationship_type: primary_employer | advisory_engagement` added to the profile schema (see Section 8 revision below) — needed immediately once BridgePoint Ops was added alongside Global Payments as a concurrent, not sequential, engagement.
- No `compliance_category` tagging yet (Phase 2, as planned) — Phase 1 evaluates content generically, not per-audience.
- **Governance flag surfaced, not yet resolved:** the Code of Conduct's AI section prohibits using AI to process/analyze confidential information without Legal + AI Governance approval — directly relevant to RB itself. Flagged in `profile.yaml` and `code-of-conduct.yaml` notes; not resolved as part of this build.

**Phase 2:**
- `compliance_category` field + heuristic inference from employer profile customer/partner lists.
- Extend the compliance check to drafted outreach messages (not just LinkedIn posts) in the action-recommendation engine.
- Remaining policy documents encoded.

**Phase 3 (deferred, revisit after Phase 1/2 prove the pattern):**
- Assisted (LLM-drafted, human-reviewed) policy extraction to reduce Phase-1/2 manual encoding cost.
- Full multi-employer archive UX (only matters once Todd changes employers again).
- `POST /compliance/check` API op if the Custom GPT needs to invoke it directly rather than relying on pipeline-embedded checks.

## Open questions for Todd

1. What policy documents do you actually have from Global Payments onboarding right now (handbook, code of conduct, social media policy, confidentiality agreement)? Phase 1 needs the real text, not assumptions.
2. Should a `MEDIUM`/`HIGH` verdict **block** the LinkedIn pipeline from surfacing a post recommendation entirely, or just attach a visible warning + rewrite suggestion and let you decide? (Recommend: block `HIGH`, warn-and-suggest on `MEDIUM`, since a false-positive block on legitimate content is a minor friction but a missed `HIGH` is the failure mode this whole feature prevents.)
3. Is there a second active income stream (BridgePoint Ops winding down per `personal_operating_system.yaml`) whose policies also need encoding now, or is Global Payments the only live employer profile needed at Phase 1?

## Success criteria

Before any LinkedIn post, drafted customer/press email, or public-facing content RB helps produce is presented as ready, it has passed through `compliance_engine.py` against the active employer's encoded policies, and the verdict (clear or flagged-with-rewrite) is visible to Todd — not silently applied, not silently skipped.
