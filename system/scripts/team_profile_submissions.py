#!/usr/bin/env python3
"""
team_profile_submissions.py — review-first queue for Team Portal-submitted
corrections to brand profiles and competitor profiles.

Ecosystem Lookup Tool project (Phase C addition, per Todd's direct
2026-09-25 instruction: "allows the team to submit updates to the
information in the portal. The info would need to be reviewed before
becoming active"). Matches the exact scan -> candidate -> confirm shape
already proven by executive_move_promotion.py / ownership_promotion.py --
just sourced from a person via Team Portal instead of an automated scan.

Deliberately NOT wired into Team Portal's own permission model: no new
"reviewer" role is added to team/manifest.yaml (an empty roster today, no
role concept at all -- inventing one is unnecessary scope for this). Any
authenticated teammate can submit; only Todd confirms, through his own
existing surface (rb_cli.py or a small API route outside
team_portal_api.py itself), matching how every other review-first pipeline
in this codebase is confirmed by Todd, never by a peer's own portal access.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

SYSTEM_DIR = SCRIPTS_DIR.parent
QUEUE_PATH = SYSTEM_DIR / ".cache" / "team_profile_submissions.json"

VALID_TARGET_TYPES = {"brand", "competitor"}


class NotFoundError(Exception):
    pass


class AlreadyReviewedError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    if not QUEUE_PATH.exists():
        return {"submissions": []}
    try:
        return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"submissions": []}


def _save(state: dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, QUEUE_PATH)


def _next_id(state: dict) -> str:
    n = 0
    for s in state["submissions"]:
        try:
            n = max(n, int(s["submission_id"].rsplit("-", 1)[-1]))
        except (KeyError, ValueError):
            pass
    return f"sub-{n + 1:05d}"


def submit(
    *,
    target_type: str,
    target_id: str,
    field_path: str,
    proposed_value: Any,
    submitted_by: str,
    note: str = "",
    source_url: str | None = None,
) -> dict:
    """A teammate's proposed correction to one field of a brand or
    competitor profile. Never writes to the live record -- only confirm()
    does that. field_path is a dotted path into the target profile (e.g.
    "identity.hq_city_state", "vulnerabilities") so confirm() knows
    exactly what to update without guessing structure."""
    if target_type not in VALID_TARGET_TYPES:
        raise ValueError(f"target_type must be one of {sorted(VALID_TARGET_TYPES)}, got {target_type!r}")
    if not target_id or not field_path:
        raise ValueError("target_id and field_path are required")

    state = _load()
    submission = {
        "submission_id": _next_id(state),
        "target_type": target_type,
        "target_id": target_id,
        "field_path": field_path,
        "proposed_value": proposed_value,
        "note": note,
        "source_url": source_url,
        "submitted_by": submitted_by,
        "submitted_at": now_iso(),
        "status": "pending",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
    }
    state["submissions"].append(submission)
    _save(state)
    return submission


def pending(*, target_type: str | None = None) -> list[dict]:
    state = _load()
    items = [s for s in state["submissions"] if s["status"] == "pending"]
    if target_type:
        items = [s for s in items if s["target_type"] == target_type]
    return items


def list_submissions(*, status: str | None = None, target_type: str | None = None) -> list[dict]:
    """Every submission, optionally filtered by status ("pending"/
    "confirmed"/"rejected") and/or target_type -- the general-purpose form
    behind Todd's review surface (rb_cli.py / rbb-chat). pending() above
    stays as the narrower, more common shortcut used by its existing
    callers."""
    state = _load()
    items = state["submissions"]
    if status:
        items = [s for s in items if s["status"] == status]
    if target_type:
        items = [s for s in items if s["target_type"] == target_type]
    return items


def get(submission_id: str) -> dict | None:
    state = _load()
    return next((s for s in state["submissions"] if s["submission_id"] == submission_id), None)


def _find_or_raise(state: dict, submission_id: str) -> dict:
    submission = next((s for s in state["submissions"] if s["submission_id"] == submission_id), None)
    if submission is None:
        raise NotFoundError(f"No submission '{submission_id}'")
    if submission["status"] != "pending":
        raise AlreadyReviewedError(f"Submission '{submission_id}' already {submission['status']}")
    return submission


def confirm(submission_id: str, *, reviewed_by: str, review_note: str = "") -> dict:
    """Marks a submission confirmed AND applies it to the live target
    record. reviewed_by should be a real identity (e.g. "todd") -- stored
    with a "human:" prefix, matching brand_profile_common.is_human_reviewed()'s
    convention, so an applied team submission is indistinguishable in
    storage from any other human-confirmed fact."""
    state = _load()
    submission = _find_or_raise(state, submission_id)
    _apply(submission, reviewed_by=reviewed_by)
    submission["status"] = "confirmed"
    submission["reviewed_by"] = reviewed_by
    submission["reviewed_at"] = now_iso()
    submission["review_note"] = review_note
    _save(state)
    return submission


def reject(submission_id: str, *, reviewed_by: str, review_note: str = "") -> dict:
    """Rejected submissions stay in the store (status="rejected") for
    audit -- never deleted."""
    state = _load()
    submission = _find_or_raise(state, submission_id)
    submission["status"] = "rejected"
    submission["reviewed_by"] = reviewed_by
    submission["reviewed_at"] = now_iso()
    submission["review_note"] = review_note
    _save(state)
    return submission


def _set_nested(record: dict, path_parts: list[str], value: Any) -> None:
    node = record
    for part in path_parts[:-1]:
        node = node.setdefault(part, {})
    node[path_parts[-1]] = value


def _apply(submission: dict, *, reviewed_by: str) -> None:
    """Writes the confirmed value into the live brand or competitor
    profile record, using the same field-provenance shape as everywhere
    else in Phase A/B.

    A competitor field in EXTENDED_PROFILE_FIELDS (products, strengths,
    weaknesses, vulnerabilities, key_customers, recent_news) is a LIST of
    individually-dated, individually-sourced entries -- a submission there
    APPENDS a new entry rather than overwriting the list, since each
    strength/weakness/news item is its own discrete, separately-sourced
    fact, not one blob with a single as-of date. Every other field
    (brand-profile fields, and competitor fields like "trends" or
    "positioning_summary") is a single value and gets overwritten."""
    import brand_profile_common as bpc
    import competitor_intelligence_common as cic

    target_type = submission["target_type"]
    target_id = submission["target_id"]
    path_parts = submission["field_path"].split(".")

    is_list_shaped = target_type == "competitor" and path_parts[0] in cic.EXTENDED_PROFILE_FIELDS

    if target_type == "brand":
        new_field = bpc.field(
            submission["proposed_value"], status="confirmed", confidence="high",
            as_of=bpc.today(), last_reviewed_by=f"human:{reviewed_by}", evidence_ids=[],
        )
        if submission.get("source_url"):
            new_field["source_url"] = submission["source_url"]
        profile = bpc.load_profile(target_id) or bpc.empty_profile(target_id, target_id)
        _set_nested(profile, path_parts, new_field)
        bpc.save_profile(target_id, profile)
        return

    d = cic.competitor_dir(target_id)
    competitor = cic.load_json(d / "competitor.json")
    new_field = cic.extended_field(
        submission["proposed_value"], status="confirmed", confidence="high",
        as_of=cic.today(), last_reviewed_by=f"human:{reviewed_by}",
        source_url=submission.get("source_url"),
    )
    if is_list_shaped:
        target_list = competitor.setdefault(path_parts[0], [])
        target_list.append(new_field)
    else:
        _set_nested(competitor, path_parts, new_field)
    competitor["updated_at"] = cic.now_iso()
    cic.save_json(d / "competitor.json", competitor)
