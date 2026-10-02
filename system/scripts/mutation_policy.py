#!/usr/bin/env python3
"""
mutation_policy.py — the single shared write-decision policy.

Built for the 2026-09-18 intelligence-cycle repair
(system/CLAUDE_HANDOFF_INTELLIGENCE_CYCLE_REPAIR_2026-09-18.md). Before this
module, at least four independent surfaces each invented their own meaning
for "pending": intelligence_mutation_engine.py (confidence-threshold only),
ecosystem_intelligence.py (date-aware, relationship-specific), ri_intake.py /
manual_relationship_intake.py (per-operation safe_to_write flags that were
computed correctly but never auto-applied for the LinkedIn passive path), and
watchlist_promotion.py (hardcoded "never auto-applies"). None of them shared
a status vocabulary, so the same underlying decision ("this is net-new,
write it") looked different depending on which ingestion path produced it,
and downstream accounting (mutations generated / auto-applied / confirmation
required / rejected) could never be reconciled across them.

Todd's canonical policy (verbatim from the handoff):
  1. Net-new information that adds a fact without replacing an existing
     canonical value is written automatically.
  2. New information that would overwrite an existing canonical value
     requires user confirmation before the overwrite.
  3. Conflicting information with dates that establish a reliable sequence
     is not an overwrite: preserve the historical fact and automatically add
     the newer dated fact/current state, with provenance.
  4. Conflicting information without dates sufficient to establish sequence
     requires user confirmation.
  5. Low-confidence identity resolution, ambiguous entity matching, or
     uncertain scope must not mutate the wrong entity; route it to review
     with a specific explanation.
  6. Every decision leaves an auditable receipt: source, observed date,
     effective date when known, old value, proposed/new value, decision
     class, confidence, resulting artifact, and whether user action is
     required.

This module implements that policy as one pure decision function
(`decide()`), plus a receipt builder and a durable JSONL receipt log that
every ingestion path can share so mutation accounting reconciles across
receipts, execution reports, and briefs (handoff §"Audit consistency").

`decide()` takes no dependency on any specific ingestion path's data shapes —
callers translate their own before/after values into the parameters below.
It does not perform any I/O itself (testable in isolation); `record_receipt`
is the one function that touches disk, and every field it writes is named
directly after the required receipt fields in the policy above.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

# Same RB_*_PATH override pattern system/tests/conftest.py already uses for
# every other module-level runtime-file constant (audit_log.py, insight_
# intake.py, ...): computed from an env var, falling back to the real
# on-disk location when unset, so conftest.py can redirect every test run
# to a per-session tmp dir before this module is ever imported instead of
# tests silently writing real mutation receipts into system/.cache/.
RECEIPTS_PATH = Path(os.environ.get("RB_MUTATION_POLICY_RECEIPTS_PATH")
                      or (core.CACHE_DIR / "mutation_policy_receipts.jsonl"))

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------

# The seven statuses the handoff names explicitly, plus one extension
# (AUTO_ADDED_HISTORICAL_FACT) for the "older dated fact -> append without
# replacing current projection" required case, which the handoff's suggested
# list doesn't give its own name -- it explicitly invites adapting the
# vocabulary "if appropriate." Every other status is exactly as named.
AUTO_ADDED_NET_NEW = "auto_added_net_new"
AUTO_ADDED_DATED_SUCCESSOR = "auto_added_dated_successor"
AUTO_ADDED_HISTORICAL_FACT = "auto_added_historical_fact"
CONFIRMATION_REQUIRED_OVERWRITE = "confirmation_required_overwrite"
CONFIRMATION_REQUIRED_UNDATED_CONFLICT = "confirmation_required_undated_conflict"
REVIEW_REQUIRED_IDENTITY_AMBIGUITY = "review_required_identity_ambiguity"
REJECTED_DUPLICATE = "rejected_duplicate"
REJECTED_LOW_CONFIDENCE = "rejected_low_confidence"

ALL_STATUSES = {
    AUTO_ADDED_NET_NEW,
    AUTO_ADDED_DATED_SUCCESSOR,
    AUTO_ADDED_HISTORICAL_FACT,
    CONFIRMATION_REQUIRED_OVERWRITE,
    CONFIRMATION_REQUIRED_UNDATED_CONFLICT,
    REVIEW_REQUIRED_IDENTITY_AMBIGUITY,
    REJECTED_DUPLICATE,
    REJECTED_LOW_CONFIDENCE,
}

# Statuses under which the caller should perform the write immediately.
AUTO_APPLY_STATUSES = {
    AUTO_ADDED_NET_NEW,
    AUTO_ADDED_DATED_SUCCESSOR,
    AUTO_ADDED_HISTORICAL_FACT,
}

# Statuses under which Todd's explicit confirmation is required before write.
CONFIRMATION_STATUSES = {
    CONFIRMATION_REQUIRED_OVERWRITE,
    CONFIRMATION_REQUIRED_UNDATED_CONFLICT,
}

# "pending intelligence" (or any other generic pending label) is not an
# acceptable status under this policy -- every path must resolve to one of
# the names above. Presentation layers MAY alias a status to friendlier
# prose, but the underlying decision class must always be one of these.
GENERIC_PENDING_ALIASES_FORBIDDEN = {"pending intelligence", "pending", "proposed"}


@dataclass(frozen=True)
class MutationDecision:
    status: str
    auto_apply: bool
    requires_user_action: bool
    reason: str
    decision_class: str  # same as status; kept separate for receipt clarity
    receipt: dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        return self.status in ALL_STATUSES


def _decision(
    status: str,
    reason: str,
    *,
    source: str,
    observed_at: str | None,
    new_date: str | None,
    existing_date: str | None,
    old_value: Any,
    new_value: Any,
    confidence: float | None,
    field_name: str | None,
    entity_id: str | None,
) -> MutationDecision:
    auto_apply = status in AUTO_APPLY_STATUSES
    requires_user_action = status in CONFIRMATION_STATUSES or status == REVIEW_REQUIRED_IDENTITY_AMBIGUITY
    receipt = {
        "source": source,
        "observed_at": observed_at,
        "effective_date": new_date or existing_date,
        "old_value": old_value,
        "new_value": new_value,
        "decision_class": status,
        "confidence": confidence,
        "field_name": field_name,
        "entity_id": entity_id,
        "requires_user_action": requires_user_action,
        "reason": reason,
    }
    return MutationDecision(
        status=status,
        auto_apply=auto_apply,
        requires_user_action=requires_user_action,
        reason=reason,
        decision_class=status,
        receipt=receipt,
    )


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def decide(
    *,
    source: str,
    new_value: Any,
    existing_value: Any = None,
    is_set_member: bool = False,
    new_date: str | None = None,
    existing_date: str | None = None,
    is_replacement: bool = False,
    identity_ambiguous: bool = False,
    identity_reason: str | None = None,
    low_confidence: bool = False,
    low_confidence_reason: str | None = None,
    observed_at: str | None = None,
    confidence: float | None = None,
    field_name: str | None = None,
    entity_id: str | None = None,
) -> MutationDecision:
    """Decide whether a proposed write should auto-apply, require
    confirmation, route to identity review, or be rejected.

    Parameters describe ONE proposed fact/value, already translated into a
    source-agnostic shape by the caller:

      is_set_member    — True when `new_value` is a member being added to a
                          list/set (e.g. a new interaction event, a new tag)
                          rather than a scalar field being replaced. Set
                          membership additions are always net-new by
                          definition (policy rule 1) once identity and
                          confidence gates pass.
      new_date/
      existing_date     — ISO date strings for the new fact and the current
                          canonical value, when known. Both present and
                          comparable is what "establishes a reliable
                          sequence" (policy rule 3) means operationally.
      is_replacement    — caller's own signal that this write is intended as
                          a direct correction/overwrite of a scalar (e.g. an
                          operator-entered correction), as distinct from two
                          pieces of information merely disagreeing. Only
                          consulted when no date sequence can resolve the
                          conflict; selects confirmation_required_overwrite
                          over confirmation_required_undated_conflict so the
                          receipt/report language matches what actually
                          happened.
      identity_ambiguous — True when entity resolution could not confidently
                          pick which canonical entity this fact belongs to.
                          Always wins over every other branch (policy rule 5):
                          an ambiguous match must not mutate either
                          candidate.
      low_confidence     — True when the caller's own quality gates (signal
                          strength thresholds, source-relevance filters, dedupe
                          against fabricated content, etc.) already decided
                          this proposal shouldn't be trusted at all, independent
                          of identity. Distinct from identity_ambiguous so
                          receipts can tell the two failure modes apart.
    """
    common = dict(
        source=source,
        observed_at=observed_at,
        new_date=new_date,
        existing_date=existing_date,
        old_value=existing_value,
        new_value=new_value,
        confidence=confidence,
        field_name=field_name,
        entity_id=entity_id,
    )

    # Rule 5 first: identity ambiguity always wins, before any other check.
    if identity_ambiguous:
        return _decision(
            REVIEW_REQUIRED_IDENTITY_AMBIGUITY,
            identity_reason or "Entity match is ambiguous or low-confidence; routed to review rather than risking a wrong-entity write.",
            **common,
        )

    if low_confidence:
        return _decision(
            REJECTED_LOW_CONFIDENCE,
            low_confidence_reason or "Proposal did not meet the minimum confidence/quality bar for this source.",
            **common,
        )

    # Exact-duplicate fact: no interruption, no write, no "pending" noise.
    if existing_value is not None and not is_set_member and _values_equal(existing_value, new_value):
        return _decision(REJECTED_DUPLICATE, "Identical to the existing canonical value; deduplicated.", **common)

    # Rule 1: adding a member to a set/list is net-new by construction.
    if is_set_member:
        return _decision(AUTO_ADDED_NET_NEW, "New set/list member; does not replace any existing canonical value.", **common)

    # Rule 1: no existing scalar value -> filling a previously-unknown field.
    if existing_value in (None, "", [], {}):
        return _decision(AUTO_ADDED_NET_NEW, "No existing canonical value for this field; net-new fact.", **common)

    # From here, existing_value is set and differs from new_value (a real
    # scalar conflict). Rule 3: if both sides are reliably dated, sequence
    # them automatically instead of treating this as an overwrite.
    if new_date and existing_date:
        if new_date > existing_date:
            return _decision(
                AUTO_ADDED_DATED_SUCCESSOR,
                f"Newer dated fact ({new_date}) supersedes the recorded value ({existing_date}); prior value preserved in history.",
                **common,
            )
        if new_date < existing_date:
            return _decision(
                AUTO_ADDED_HISTORICAL_FACT,
                f"Older dated fact ({new_date}) than the current canonical value ({existing_date}); appended to history without changing current state.",
                **common,
            )
        # Same date, different value: dates cannot resolve which is correct.
        return _decision(
            CONFIRMATION_REQUIRED_UNDATED_CONFLICT,
            f"Two different values both dated {new_date}; cannot establish which is correct without confirmation.",
            **common,
        )

    # Rule 4: no reliable date sequence available.
    if is_replacement:
        return _decision(
            CONFIRMATION_REQUIRED_OVERWRITE,
            "Proposed write would replace an existing canonical value; confirmation required before overwrite.",
            **common,
        )
    return _decision(
        CONFIRMATION_REQUIRED_UNDATED_CONFLICT,
        "Conflicting value with no date sufficient to establish which is current; confirmation required.",
        **common,
    )


# ---------------------------------------------------------------------------
# Durable receipts
# ---------------------------------------------------------------------------

def record_receipt(
    decision: MutationDecision,
    *,
    artifact: str | None = None,
    applied: bool | None = None,
    path: Path | None = None,
) -> dict:
    """Append one durable receipt for a decision to RECEIPTS_PATH (or
    `path`, for tests). Every write this module's callers make should have
    exactly one receipt here, whether or not the write actually happened --
    a rejected/review-required decision still gets a receipt explaining why
    nothing was written.

    `artifact` names the file/store the write landed in (None until the
    caller has actually performed the write, e.g. for a decision requiring
    confirmation). `applied` records whether a durable write actually
    happened -- distinct from `decision.auto_apply`, which only says the
    policy *permits* auto-apply; the caller may still fail to write (a
    downstream exception, a rollback) and should report that honestly here
    rather than letting `auto_apply` stand in for "it happened."
    """
    p = path or RECEIPTS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    record = dict(decision.receipt)
    record["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record["artifact"] = artifact
    record["applied"] = bool(applied) if applied is not None else (decision.auto_apply and artifact is not None)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return record


def load_receipts(*, since: str | None = None, path: Path | None = None) -> list[dict]:
    p = path or RECEIPTS_PATH
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and str(row.get("recorded_at") or "") < since:
            continue
        out.append(row)
    return out


def summarize_receipts(receipts: list[dict]) -> dict:
    """Reconciliation summary: counts per decision class, matching the
    fields the handoff requires to agree across receipts/reports/briefs
    (mutations generated, automatically applied, confirmation required,
    rejected/deduplicated, records changed)."""
    by_status: dict[str, int] = {}
    for r in receipts:
        status = r.get("decision_class") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
    auto_applied = sum(1 for r in receipts if r.get("applied"))
    confirmation_required = sum(
        1 for r in receipts if r.get("decision_class") in CONFIRMATION_STATUSES
    )
    review_required = sum(
        1 for r in receipts if r.get("decision_class") == REVIEW_REQUIRED_IDENTITY_AMBIGUITY
    )
    rejected = sum(
        1 for r in receipts
        if r.get("decision_class") in {REJECTED_DUPLICATE, REJECTED_LOW_CONFIDENCE}
    )
    return {
        "mutations_generated": len(receipts),
        "automatically_applied": auto_applied,
        "confirmation_required": confirmation_required,
        "review_required": review_required,
        "rejected": rejected,
        "records_changed": auto_applied,
        "by_status": by_status,
    }
