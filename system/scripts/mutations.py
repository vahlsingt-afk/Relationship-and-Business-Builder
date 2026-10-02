#!/usr/bin/env python3
"""
mutations.py — write-back operations on canonical RB files.

This is the surface a session uses to record what happened — close a loop,
update a last_touch, add a contact, open or close an active thread. Every
mutation:

    1. Snapshots the file being changed into system/_snapshots/.
    2. Applies the change.
    3. Validates (where applicable) and rolls back on failure.

Usage:
    python3 mutations.py loop-add \
        --party "Mike Schwartz" \
        --description "Send updated resume." \
        --target 2026-05-22

    python3 mutations.py loop-close \
        --id L-2026-05-12-003 \
        --reason "Phone screen captured 2026-05-15; promoted Mike to LKI."

    python3 mutations.py touch \
        --id bruce-sellnow \
        --date 2026-05-15

    python3 mutations.py contact-add \
        --id mike-schwartz \
        --name "Mike Schwartz" \
        --company "Global Payments Inc." \
        --signal-class LKI

    python3 mutations.py thread-open \
        --id T-2026-05-new-thread \
        --title "..." --type business_engagement

    python3 mutations.py thread-close \
        --id T-2026-05-new-thread \
        --reason "Done."

All commands accept --dry-run to preview the change without writing.

Exit codes:
    0 — success
    1 — validation failure (file untouched after rollback)
    2 — bad arguments
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core
import opportunity_pipeline
import eolms

SCHEMA_VALIDATOR = core.SYSTEM_DIR / "schemas" / "validate.py"


# -----------------------------------------------------------------------------
# Snapshot helpers
# -----------------------------------------------------------------------------

def snapshot(path: Path, tag: str) -> Path:
    """Copy `path` to system/_snapshots/<stem>.<tag>.<ext>."""
    core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix
    stem = path.stem
    dst = core.SNAPSHOTS_DIR / f"{stem}.{tag}{suffix}"
    shutil.copy2(path, dst)
    return dst


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# -----------------------------------------------------------------------------
# Loop ledger ops
# -----------------------------------------------------------------------------

def _read_ledger() -> str:
    return core.LOOP_LEDGER_PATH.read_text()


def _write_ledger(text: str) -> None:
    core.LOOP_LEDGER_PATH.write_text(text)


def _next_loop_id(today: date) -> str:
    """Return the next available L-YYYY-MM-DD-NNN for today."""
    prefix = f"L-{today.isoformat()}-"
    used = re.findall(re.escape(prefix) + r"(\d{3})", _read_ledger())
    if not used:
        n = 1
    else:
        n = max(int(x) for x in used) + 1
    return f"{prefix}{n:03d}"


def cmd_loop_add(args) -> int:
    today = date.fromisoformat(args.opened) if args.opened else date.today()
    target = date.fromisoformat(args.target)
    loop_id = args.id or _next_loop_id(today)
    new_row = (
        f"| {loop_id} | {today.isoformat()} | {args.party} | {args.description} | "
        f"{target.isoformat()} | open |"
    )
    text = _read_ledger()
    # Insert before the "Closed / abandoned" section if present; else append.
    closed_idx = text.find("\n## Closed")
    if closed_idx < 0:
        new_text = text.rstrip() + "\n" + new_row + "\n"
    else:
        new_text = text[:closed_idx].rstrip() + "\n" + new_row + "\n" + text[closed_idx:]
    if args.dry_run:
        print(new_row)
        return 0
    snapshot(core.LOOP_LEDGER_PATH, f"pre-loop-add-{loop_id}-{_now_tag()}")
    _write_ledger(new_text)
    print(f"Added {loop_id}.")
    return 0


def cmd_loop_redate(args) -> int:
    """Update the target date on an open loop.

    Usage:
        python3 mutations.py loop-redate --id L-2026-05-08-020 --target 2026-06-18
        python3 mutations.py loop-redate --id L-2026-05-08-020 --target 2026-06-18 \\
            --note "Re-dated: scenario prep deferred — Patrick Nelson call pending"
    """
    text = _read_ledger()
    if args.id not in text:
        print(f"ERROR: loop id {args.id!r} not found in ledger.", file=sys.stderr)
        return 1
    try:
        new_target = date.fromisoformat(args.target)
    except ValueError:
        print(f"ERROR: invalid target date {args.target!r}. Use YYYY-MM-DD.", file=sys.stderr)
        return 1

    lines = text.splitlines()
    out = []
    changed = False
    for line in lines:
        if line.startswith(f"| {args.id} |") and "open" in line.lower():
            # Row: | id | opened | party | desc | target | status |
            # Split on " | " to find the target field (index 4)
            parts = line.split(" | ")
            if len(parts) >= 6:
                old_target = parts[4].strip()
                parts[4] = f" {new_target.isoformat()} "
                line = " | ".join(parts)
                changed = True
                if args.note:
                    # Append note to description field (index 3)
                    desc = parts[3].strip()
                    parts[3] = f" {desc} [Re-dated {date.today().isoformat()} from {old_target}: {args.note}] "
                    line = " | ".join(parts)
        out.append(line)

    if not changed:
        print(f"ERROR: could not parse row for {args.id}; is it closed or already at that date?",
              file=sys.stderr)
        return 1

    new_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    if args.dry_run:
        print(f"Would redate {args.id} to {new_target.isoformat()}")
        if args.note:
            print(f"  Note: {args.note}")
        return 0

    snapshot(core.LOOP_LEDGER_PATH, f"pre-loop-redate-{args.id}-{_now_tag()}")
    _write_ledger(new_text)
    print(f"Redated {args.id} → {new_target.isoformat()}.")
    return 0


def cmd_loop_close(args) -> int:
    text = _read_ledger()
    if args.id not in text:
        print(f"ERROR: loop id {args.id!r} not found in ledger.", file=sys.stderr)
        return 1
    lines = text.splitlines()
    out = []
    changed = False
    for line in lines:
        if line.startswith(f"| {args.id} |") and "open" in line.lower():
            # Replace the last "open" status segment with the close note
            new_status = f"**closed** — {args.reason}"
            # Find the last "| open |" or "| open" and replace
            parts = line.rsplit("|", 2)
            # parts: ['... target | ', ' open ', '']
            if len(parts) == 3 and "open" in parts[1].lower():
                line = parts[0] + "| " + new_status + " |" + parts[2]
                changed = True
        out.append(line)
    if not changed:
        print(f"ERROR: could not parse status field for {args.id}; "
              "is it already closed?", file=sys.stderr)
        return 1
    new_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    if args.dry_run:
        print(f"Would close {args.id} with reason: {args.reason}")
        return 0
    snapshot(core.LOOP_LEDGER_PATH, f"pre-loop-close-{args.id}-{_now_tag()}")
    _write_ledger(new_text)
    print(f"Closed {args.id}.")
    # Keep the EOLMS register in sync — closeLoop only writes loop_ledger.md
    # by default, but getRenderedDailyBrief's Executive Status view reads
    # eolms/loops.json, so a loop closed only here would still render open.
    try:
        eolms_result = eolms.close_by_ledger_id(args.id, args.reason)
        if eolms_result:
            print(f"Also closed matching EOLMS entry {eolms_result['id']}.")
    except Exception as e:  # noqa: BLE001 — EOLMS sync is additive, never blocks the ledger close
        print(f"WARNING: EOLMS sync failed for {args.id}: {e}", file=sys.stderr)
    return 0


# -----------------------------------------------------------------------------
# Baseline ops
# -----------------------------------------------------------------------------

def _validate_baseline_or_rollback(snapshot_path: Path) -> int:
    # RB-2026-08-28: was a bare "python3" -- resolves via PATH, not
    # necessarily the same interpreter/environment running this process.
    # Confirmed live: in production (api-server run with PYTHONNOUSERSITE=1
    # + PYTHONPATH=vendor/py39), "python3" on PATH resolved to /usr/bin/
    # python3, which doesn't see the vendored jsonschema at all -- every
    # touchContact call failed with "jsonschema not installed", classified
    # identically to a real schema failure, silently rolled back every
    # time. sys.executable is guaranteed to be the same interpreter
    # currently running this process, so it inherits the same environment
    # this process was actually launched with.
    rc = subprocess.run(
        [sys.executable, str(SCHEMA_VALIDATOR)],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        sys.stderr.write(rc.stderr or rc.stdout)
        # Rollback
        shutil.copy2(snapshot_path, core.BASELINE_PATH)
        sys.stderr.write(f"\nROLLBACK: restored from {snapshot_path}\n")
        return 1
    return 0


def _update_card_last_touch(card_path: Path, new_date_iso: str) -> dict:
    """Update the YAML frontmatter `last_touch` field on an RC card in place.

    Returns a result dict:
        {
          "exists": bool,                # card file existed
          "changed": bool,               # file was actually rewritten
          "prior": str | None,           # prior frontmatter value or None
          "frontmatter_block": bool,     # card had a parseable --- ... --- block
          "field_present": bool,         # last_touch line existed before mutation
          "reason": str | None,          # disposition: in_sync, added, replaced,
                                         # no_frontmatter, no_field_inserted_skipped
        }

    The intent is to keep the materialized RC card projection in sync with the
    canonical baseline_index.json `last_touch` value. See
    TOUCHCONTACT-PROJECTION-SYNC-001 (2026-05-18 trace) for the defect this
    closes.
    """
    if not card_path.exists():
        return {
            "exists": False, "changed": False, "prior": None,
            "frontmatter_block": False, "field_present": False,
            "reason": "no_card_exists",
        }
    text = card_path.read_text()
    fm_match = re.match(r"^---\n(.*?\n)---\n", text, flags=re.DOTALL)
    if not fm_match:
        return {
            "exists": True, "changed": False, "prior": None,
            "frontmatter_block": False, "field_present": False,
            "reason": "no_frontmatter_block",
        }
    body_start = fm_match.end()
    fm_inner = fm_match.group(1)
    field_re = re.compile(r"^last_touch:\s*(.*)$", re.MULTILINE)
    m = field_re.search(fm_inner)
    if m:
        prior = m.group(1).strip()
        if prior == new_date_iso:
            return {
                "exists": True, "changed": False, "prior": prior,
                "frontmatter_block": True, "field_present": True,
                "reason": "in_sync",
            }
        new_inner = field_re.sub(f"last_touch: {new_date_iso}", fm_inner, count=1)
        reason = "replaced"
        field_present = True
    else:
        prior = None
        if not fm_inner.endswith("\n"):
            fm_inner = fm_inner + "\n"
        new_inner = fm_inner + f"last_touch: {new_date_iso}\n"
        reason = "added"
        field_present = False
    new_text = f"---\n{new_inner}---\n" + text[body_start:]
    card_path.write_text(new_text)
    return {
        "exists": True, "changed": True, "prior": prior,
        "frontmatter_block": True, "field_present": field_present,
        "reason": reason,
    }


def touch_contact(
    contact_id: str,
    new_date_iso: Optional[str] = None,
    source: Optional[str] = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Apply a `touch` mutation: update baseline + RC card frontmatter atomically.

    Order of operations (mirrors the protocol in P-009):
      1. Resolve and validate the target baseline entry exists.
      2. Snapshot + write baseline_index.json with the new last_touch.
      3. Validate baseline; rollback on failure.
      4. If system/cards/{id}.md exists, snapshot the card and rewrite its
         YAML frontmatter `last_touch` field. Card writes never roll back the
         baseline — projection sync is best-effort and surfaced in the result.
      5. Return a structured result containing both canonical and projection
         outcomes so the API/CLI/MCP surfaces can report them.

    Raises ValueError if the contact id is not in the baseline.
    Raises RuntimeError if the baseline write fails validation (after rollback).
    """
    new_date = (
        date.fromisoformat(new_date_iso) if new_date_iso else date.today()
    )
    iso = new_date.isoformat()
    baseline = core.load_baseline()
    target = None
    for e in baseline:
        if e.get("id") == contact_id:
            target = e
            break
    if target is None:
        raise ValueError(f"no entry with id={contact_id!r}")

    prior = target.get("last_touch")
    target["last_touch"] = iso
    if source:
        srcs = list(target.get("sources") or [])
        if source not in srcs:
            srcs.append(source)
        target["sources"] = srcs

    card_path = core.CARDS_DIR / f"{contact_id}.md"

    if dry_run:
        # Simulate card check without writing.
        if card_path.exists():
            text = card_path.read_text()
            m = re.search(
                r"^---\n(.*?\n)---\n", text, flags=re.DOTALL,
            )
            if m:
                card_prior_m = re.search(
                    r"^last_touch:\s*(.*)$", m.group(1), re.MULTILINE,
                )
                card_prior = card_prior_m.group(1).strip() if card_prior_m else None
            else:
                card_prior = None
            card_exists = True
        else:
            card_prior = None
            card_exists = False
        return {
            "ok": True,
            "id": contact_id,
            "last_touch": iso,
            "prior_last_touch": prior,
            "baseline_updated": False,
            "card_exists": card_exists,
            "card_updated": False,
            "card_prior_last_touch": card_prior,
            "card_reason": "dry_run",
            "cache_refresh_recommended": False,
            "dry_run": True,
        }

    snap = snapshot(core.BASELINE_PATH, f"pre-touch-{contact_id}-{_now_tag()}")
    core.BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    if _validate_baseline_or_rollback(snap) != 0:
        raise RuntimeError(
            f"baseline validation failed after touch {contact_id!r}; "
            "rolled back to snapshot"
        )

    card_result: dict
    if card_path.exists():
        # Snapshot the card before rewriting frontmatter. We do this even when
        # in_sync to keep the audit trail aligned with the baseline snapshot.
        snapshot(card_path, f"pre-touch-{contact_id}-{_now_tag()}")
        try:
            card_result = _update_card_last_touch(card_path, iso)
        except Exception as exc:  # noqa: BLE001
            # Best-effort: do not roll back the baseline. Surface the failure.
            card_result = {
                "exists": True, "changed": False, "prior": None,
                "frontmatter_block": False, "field_present": False,
                "reason": f"card_update_failed:{exc}",
            }
    else:
        card_result = {
            "exists": False, "changed": False, "prior": None,
            "frontmatter_block": False, "field_present": False,
            "reason": "no_card_exists",
        }

    return {
        "ok": True,
        "id": contact_id,
        "last_touch": iso,
        "prior_last_touch": prior,
        "baseline_updated": True,
        "card_exists": card_result["exists"],
        "card_updated": card_result["changed"],
        "card_prior_last_touch": card_result["prior"],
        "card_reason": card_result["reason"],
        # We do not maintain an explicit projection cache today, but signal to
        # downstream readers that they should treat any in-memory snapshot of
        # the card as potentially stale.
        "cache_refresh_recommended": card_result["changed"],
    }


def cmd_touch(args) -> int:
    """CLI wrapper around touch_contact()."""
    try:
        result = touch_contact(
            args.id, args.date, args.source, dry_run=bool(args.dry_run),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(
            f"{args.id}: last_touch {result['prior_last_touch']!r} -> "
            f"{result['last_touch']!r} (card_exists={result['card_exists']})"
        )
        return 0
    card_note = ""
    if result["card_exists"]:
        if result["card_updated"]:
            card_note = (
                f"; card frontmatter {result['card_prior_last_touch']!r} -> "
                f"{result['last_touch']!r} ({result['card_reason']})"
            )
        else:
            card_note = f"; card frontmatter unchanged ({result['card_reason']})"
    else:
        card_note = "; no RC card to project to"
    print(
        f"{args.id}: last_touch -> {result['last_touch']} "
        f"(was {result['prior_last_touch']!r}){card_note}."
    )
    return 0


def update_contact_fields(
    contact_id: str, *, current_company: Optional[str] = None, current_role: Optional[str] = None,
    source: Optional[str] = None, dry_run: bool = False,
    employment_confidence: Optional[dict] = None, confirmed_by: Optional[str] = None,
) -> dict:
    """RB-2026-09-11: clean, importable field-update function for callers
    that already have real values in hand (executive_move_promotion.py's
    record_proposal(), specifically) -- same write sequence
    cmd_contact_update() already uses (snapshot, direct field assignment,
    _validate_baseline_or_rollback), just without that function's CLI
    argparse.Namespace shape. Only fields explicitly passed (non-None) are
    modified; omitted fields are left untouched. Does NOT maintain
    company_history/title_history -- confirmed nothing in this codebase
    populates those today despite the schema defining them; not
    introduced here either.

    employment_confidence/confirmed_by (Confidence-Based Auto-Recording
    Phase 6, 2026-09-25): written alongside current_company/current_role in
    the SAME validated write, so executive_move_promotion.py's record_
    proposal() doesn't need a second write pass just to stamp provenance.
    Both are no-ops (omit entirely) for every other caller.

    Raises ValueError if contact_id is not in the baseline.
    """
    baseline = core.load_baseline(core.BASELINE_PATH)  # explicit path -- load_baseline's default binds at rb_core.py's own import time, not per-call
    entry = next((e for e in baseline if e.get("id") == contact_id), None)
    if entry is None:
        raise ValueError(f"no entry with id={contact_id!r}")

    changed: list[str] = []
    if current_company is not None:
        entry["current_company"] = current_company
        changed.append("current_company")
    if current_role is not None:
        entry["current_role"] = current_role
        changed.append("current_role")
    if employment_confidence is not None:
        entry["employment_confidence"] = employment_confidence
        changed.append("employment_confidence")
    if confirmed_by is not None:
        entry["current_company_confirmed_by"] = confirmed_by
        changed.append("current_company_confirmed_by")
    if source:
        srcs = list(entry.get("sources") or [])
        if source not in srcs:
            srcs.append(source)
        entry["sources"] = srcs

    if dry_run:
        return {"ok": True, "id": contact_id, "changed": changed, "dry_run": True}

    if not changed:
        return {"ok": True, "id": contact_id, "changed": changed}

    snap = snapshot(core.BASELINE_PATH, f"pre-update-{contact_id}-{_now_tag()}")
    core.BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    if _validate_baseline_or_rollback(snap) != 0:
        return {"ok": False, "id": contact_id, "error": "schema validation failed, rolled back"}
    return {"ok": True, "id": contact_id, "changed": changed}


def add_reported_alternate(contact_id: str, alternate: dict, *, dry_run: bool = False) -> dict:
    """Confidence-Based Auto-Recording Phase 6 (2026-09-25): append a
    sourced-but-not-yet-stronger-than-incumbent employment claim to a
    contact's own reported_alternates list, without touching current_
    company/current_role -- the baseline_index.json twin of
    ecosystem_intelligence.py entities' own reported_alternates (Phase 6's
    ownership_promotion.py). Same validated-write discipline as
    update_contact_fields(). Never de-dupes on content -- executive_move_
    promotion.py's record_proposal() already guarantees candidate_id
    uniqueness upstream, so a re-run of the same scan never re-appends."""
    baseline = core.load_baseline(core.BASELINE_PATH)
    entry = next((e for e in baseline if e.get("id") == contact_id), None)
    if entry is None:
        raise ValueError(f"no entry with id={contact_id!r}")

    if dry_run:
        return {"ok": True, "id": contact_id, "dry_run": True}

    entry.setdefault("reported_alternates", []).append(alternate)
    snap = snapshot(core.BASELINE_PATH, f"pre-alternate-{contact_id}-{_now_tag()}")
    core.BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    if _validate_baseline_or_rollback(snap) != 0:
        return {"ok": False, "id": contact_id, "error": "schema validation failed, rolled back"}
    return {"ok": True, "id": contact_id}


def cmd_contact_update(args) -> int:
    """Update mutable fields on an existing baseline_index.json entry.

    Only fields explicitly passed are modified.  Omitted fields are unchanged.
    Always snapshots before writing so the change is rollback-safe.

    Updateable fields: current_company, current_role, email, phone, linkedin_url,
    last_touch, signal_class, rc_tier, rc_state, notes (append or replace), tags (add).

    Usage:
        python3 mutations.py contact-update --id amy-spytko \\
            --company "QSRSoft" --role "VP of Sales" \\
            --notes "Confirmed move from BridgePoint to QSRSoft (VP Sales) — LinkedIn 2026-03-16"
    """
    baseline = core.load_baseline()
    entry = next((e for e in baseline if e.get("id") == args.id), None)
    if entry is None:
        print(f"ERROR: id {args.id!r} not found in baseline.", file=sys.stderr)
        return 1

    changed: list[str] = []

    if args.company is not None:
        entry["current_company"] = args.company
        changed.append(f"current_company={args.company!r}")

    if args.role is not None:
        entry["current_role"] = args.role
        changed.append(f"current_role={args.role!r}")

    if args.email is not None:
        entry["email"] = args.email
        changed.append(f"email={args.email!r}")

    if args.phone is not None:
        entry["phone"] = args.phone
        changed.append(f"phone={args.phone!r}")

    if args.linkedin is not None:
        entry["linkedin_url"] = args.linkedin
        changed.append(f"linkedin_url={args.linkedin!r}")

    if args.last_touch is not None:
        entry["last_touch"] = args.last_touch
        changed.append(f"last_touch={args.last_touch!r}")

    if args.signal_class is not None:
        entry["signal_class"] = args.signal_class
        changed.append(f"signal_class={args.signal_class!r}")

    if args.rc_tier is not None:
        entry["rc_tier"] = args.rc_tier
        changed.append(f"rc_tier={args.rc_tier!r}")

    if args.rc_state is not None:
        entry["rc_state"] = args.rc_state
        changed.append(f"rc_state={args.rc_state!r}")

    if args.relationship_domain is not None:
        entry["relationship_domain"] = args.relationship_domain
        changed.append(f"relationship_domain={args.relationship_domain!r}")

    if args.notes:
        prior = (entry.get("notes") or "").rstrip()
        if args.notes_replace:
            entry["notes"] = args.notes
        else:
            today_tag = f"[{date.today().isoformat()}]"
            entry["notes"] = (f"{prior}\n{today_tag} {args.notes}").lstrip()
        changed.append("notes(updated)")

    if args.tags_add:
        existing = entry.get("tags") or []
        new_tags = [t for t in args.tags_add if t not in existing]
        entry["tags"] = existing + new_tags
        if new_tags:
            changed.append(f"tags+={new_tags}")

    if not changed:
        print(f"No fields to update for {args.id}. Pass at least one field flag.")
        return 1

    if args.dry_run:
        print(f"DRY RUN — would update {args.id}:")
        for c in changed:
            print(f"  {c}")
        print(json.dumps(entry, indent=2))
        return 0

    snap = snapshot(core.BASELINE_PATH, f"pre-update-{args.id}-{_now_tag()}")
    core.BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    if _validate_baseline_or_rollback(snap) != 0:
        return 1

    print(f"Updated {args.id}: {', '.join(changed)}")
    return 0


def cmd_contact_set_employment_state(args) -> int:
    """Set the full employment-state field set on an existing baseline entry
    -- current_company/current_role plus the employment_status/last_known_*
    tracking fields introduced by the LinkedIn ended-role cleanup (2026-08-06,
    see system/scripts/employment_state.py).

    A dedicated command rather than cmd_contact_update's field-flags, because
    this transition explicitly SETS current_company/current_role to null
    (the contact is no longer there) -- cmd_contact_update's "None means
    field not supplied, leave unchanged" convention can't express "clear
    this field", only "don't touch it".

    Usage:
        python3 mutations.py contact-set-employment-state --id sal-nazir \\
            --employment-status no_stated_current_role \\
            --employment-status-source operator_confirmed \\
            --employment-date-confidence operator_confirmed \\
            --employment-end-date 2026-05-01 \\
            --last-known-company "PAR Technology" --last-known-role "General Manager, Payments"
    """
    baseline = core.load_baseline()
    entry = next((e for e in baseline if e.get("id") == args.id), None)
    if entry is None:
        print(f"ERROR: id {args.id!r} not found in baseline.", file=sys.stderr)
        return 1

    entry["current_company"] = None
    entry["current_role"] = None
    entry["employment_status"] = args.employment_status
    entry["employment_status_source"] = args.employment_status_source
    entry["employment_status_observed_at"] = args.employment_status_observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry["employment_end_date"] = args.employment_end_date
    entry["employment_date_confidence"] = args.employment_date_confidence
    if args.last_known_company:
        entry["last_known_company"] = args.last_known_company
    if args.last_known_role:
        entry["last_known_role"] = args.last_known_role

    if args.dry_run:
        print(f"DRY RUN — would set employment state for {args.id}:")
        print(json.dumps(entry, indent=2))
        return 0

    snap = snapshot(core.BASELINE_PATH, f"pre-employment-state-{args.id}-{_now_tag()}")
    core.BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    if _validate_baseline_or_rollback(snap) != 0:
        return 1

    print(f"Set employment state for {args.id}: {args.employment_status} "
          f"(was {args.last_known_company or 'unknown'}, source={args.employment_status_source})")
    return 0


def cmd_bulk_apply_pending(args) -> int:
    """Apply all pending LinkedIn-detected company/role changes in bulk.

    Scans baseline for entries where notes contain 'now at X as of DATE' and
    current_company doesn't match — then applies the update automatically.

    Only applies to RC and LKI contacts unless --signal-class overrides.
    Always snapshots before writing. Use --dry-run first to preview.

    Usage:
        python3 mutations.py bulk-apply-pending --dry-run          # preview
        python3 mutations.py bulk-apply-pending --signal-class LKI # apply LKI only
        python3 mutations.py bulk-apply-pending                    # apply RC + LKI
    """
    import re as _re

    NOW_AT_RE = _re.compile(r'now at ([^.]+?) as of (\d{4}-\d{2}-\d{2})', _re.IGNORECASE)
    RESOLVED_MARKERS = ('conflict resolved', 'confirmed by todd', 'confirmed by operator',
                        'company-change-confirmed', 'role-change-confirmed')

    target_classes = {args.signal_class} if args.signal_class else {"RC", "LKI"}

    baseline = core.load_baseline()
    updates: list[dict] = []

    for entry in baseline:
        if entry.get("signal_class") not in target_classes:
            continue
        notes = entry.get("notes") or ""
        notes_lower = notes.lower()
        if any(m in notes_lower for m in RESOLVED_MARKERS):
            continue
        tags = [t.lower() for t in (entry.get("tags") or [])]
        if any(t in tags for t in ('company-change-confirmed', 'role-change-confirmed')):
            continue

        current_co = (entry.get("current_company") or "").strip()
        m = NOW_AT_RE.search(notes)
        if not m:
            continue
        notes_co = m.group(1).strip()
        detected_date = m.group(2)

        co_match = (
            notes_co.lower() in current_co.lower()
            or current_co.lower() in notes_co.lower()
            or not notes_co
        )
        if co_match:
            continue

        updates.append({
            "entry": entry,
            "old_company": current_co,
            "new_company": notes_co,
            "detected_date": detected_date,
        })

    if not updates:
        print("No pending company updates found.")
        return 0

    print(f"Found {len(updates)} pending update(s):")
    for u in updates:
        e = u["entry"]
        print(f"  [{e.get('signal_class')}/{e.get('rc_tier') or ''}] {e.get('name')}: "
              f"{u['old_company']!r} → {u['new_company']!r} (detected {u['detected_date']})")

    if args.dry_run:
        print(f"\nDRY RUN — {len(updates)} update(s) not applied. Remove --dry-run to apply.")
        return 0

    # Apply updates
    snap = snapshot(core.BASELINE_PATH, f"pre-bulk-apply-{_now_tag()}")
    for u in updates:
        entry = u["entry"]
        entry["current_company"] = u["new_company"]
        existing_tags = entry.get("tags") or []
        if "company-change-confirmed" not in existing_tags:
            entry.setdefault("tags", []).append("company-change-confirmed")
        prior = (entry.get("notes") or "").rstrip()
        today_tag = f"[{date.today().isoformat()}]"
        entry["notes"] = (
            f"{prior}\n{today_tag} company-change-confirmed: "
            f"{u['old_company']} → {u['new_company']} "
            f"(bulk-apply-pending, detected {u['detected_date']})"
        ).lstrip()

    core.BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    if _validate_baseline_or_rollback(snap) != 0:
        return 1

    print(f"\nApplied {len(updates)} update(s) successfully.")
    return 0


def cmd_contact_add(args) -> int:
    """Add a new entry to baseline_index.json."""
    baseline = core.load_baseline()
    if any(e.get("id") == args.id for e in baseline):
        print(f"ERROR: id {args.id!r} already exists in baseline.", file=sys.stderr)
        return 1
    entry = {
        "id": args.id,
        "name": args.name,
        "current_company": args.company,
        "current_role": args.role,
        "location": None,
        "linkedin_url": args.linkedin,
        "email": args.email,
        "phone": args.phone,
        "sources": [args.source] if args.source else ["mutations.py"],
        "linkedin_connected_on": None,
        "signal_class": args.signal_class,
        "rc_state": "ACTIVE" if args.signal_class == "RC" else None,
        "rc_tier": args.rc_tier if args.signal_class == "RC" else None,
        "last_touch": args.last_touch,
        "circles": [],
        "tags": [],
        "notes": args.notes or "",
    }
    if args.dry_run:
        print(json.dumps(entry, indent=2))
        return 0
    baseline.append(entry)
    snap = snapshot(core.BASELINE_PATH, f"pre-add-{args.id}-{_now_tag()}")
    core.BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    if _validate_baseline_or_rollback(snap) != 0:
        return 1
    print(f"Added {args.id} as {args.signal_class}.")
    return 0


# -----------------------------------------------------------------------------
# Strategic-operator ops (RB 9.1)
# -----------------------------------------------------------------------------

def _read_operators_file() -> tuple[dict, str]:
    """Return (parsed dict, raw text). Empty skeleton when missing."""
    if not core.STRATEGIC_OPERATORS_PATH.exists():
        return (
            {"version": 1, "contract": "rb_strategic_operators_v1", "operators": []},
            "",
        )
    text = core.STRATEGIC_OPERATORS_PATH.read_text()
    parsed = core._yaml_load(text) or {}
    # YAML auto-coerces ISO dates to datetime.date — stringify before
    # downstream code uses them.
    from datetime import date as _date, datetime as _datetime

    def _stringify(o):
        if isinstance(o, _datetime):
            return o.isoformat()
        if isinstance(o, _date):
            return o.isoformat()
        if isinstance(o, dict):
            return {k: _stringify(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_stringify(v) for v in o]
        return o

    return _stringify(parsed), text


def _write_operators_file(data: dict) -> None:
    try:
        import yaml  # type: ignore
    except ImportError:
        sys.stderr.write(
            "PyYAML required to write strategic_operators.yaml. "
            "Install with: pip install pyyaml --break-system-packages\n"
        )
        raise
    data["version"] = data.get("version", 1)
    data["contract"] = "rb_strategic_operators_v1"
    data["last_updated"] = date.today().isoformat()
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
    core.STRATEGIC_OPERATORS_PATH.write_text(text)


def _validate_operators_or_rollback(snapshot_path: Path) -> int:
    """Run schemas/validate.py against strategic_operators.yaml; rollback on failure."""
    # Same fix as _validate_baseline_or_rollback -- see its comment.
    rc = subprocess.run(
        [
            sys.executable, str(SCHEMA_VALIDATOR),
            str(core.STRATEGIC_OPERATORS_PATH),
            "--schema", str(core.SYSTEM_DIR / "schemas" / "strategic_operators.schema.json"),
        ],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        sys.stderr.write(rc.stderr or rc.stdout)
        shutil.copy2(snapshot_path, core.STRATEGIC_OPERATORS_PATH)
        sys.stderr.write(f"\nROLLBACK: restored strategic_operators.yaml from {snapshot_path}\n")
        return 1
    return 0


def _slug(text: str, *, maxlen: int = 24) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (cleaned or "movement")[:maxlen]


def _movement_id(event_at: str, summary: str) -> str:
    return f"M-{event_at}-{_slug(summary)}"


def cmd_operator_add(args) -> int:
    data, _ = _read_operators_file()
    operators = data.setdefault("operators", [])
    if any(op.get("id") == args.id for op in operators):
        print(f"ERROR: operator {args.id!r} already exists.", file=sys.stderr)
        return 1
    new_op = {
        "id": args.id,
        "name": args.name,
        "entity_type": args.entity_type,
        "watchlist_bucket": args.watchlist_bucket,
        "status": "active",
        "opened": date.today().isoformat(),
        "last_movement_at": None,
        "companies_owned": args.companies_owned or [],
        "brands_in_portfolio": args.brands or [],
        "executives": args.executives or [],
        "vendor_relationships": [],
        "relationship_proximity": args.relationship_proximity or "none",
        "proximity_evidence": [],
        "notes": args.notes or "",
        "movements": [],
    }
    if args.dry_run:
        print(json.dumps(new_op, indent=2))
        return 0
    operators.append(new_op)
    snap = snapshot(core.STRATEGIC_OPERATORS_PATH, f"pre-operator-add-{args.id}-{_now_tag()}") \
        if core.STRATEGIC_OPERATORS_PATH.exists() else None
    _write_operators_file(data)
    if snap and _validate_operators_or_rollback(snap) != 0:
        return 1
    print(f"Added operator {args.id}.")
    return 0


def cmd_operator_update(args) -> int:
    data, _ = _read_operators_file()
    operators = data.get("operators") or []
    target = next((op for op in operators if op.get("id") == args.id), None)
    if target is None:
        print(f"ERROR: no operator with id={args.id!r}.", file=sys.stderr)
        return 1
    diff: dict = {}
    if args.name is not None:
        diff["name"] = (target.get("name"), args.name)
        target["name"] = args.name
    if args.entity_type is not None:
        diff["entity_type"] = (target.get("entity_type"), args.entity_type)
        target["entity_type"] = args.entity_type
    if args.watchlist_bucket is not None:
        diff["watchlist_bucket"] = (target.get("watchlist_bucket"), args.watchlist_bucket)
        target["watchlist_bucket"] = args.watchlist_bucket
    if args.relationship_proximity is not None:
        diff["relationship_proximity"] = (
            target.get("relationship_proximity"), args.relationship_proximity,
        )
        target["relationship_proximity"] = args.relationship_proximity
    if args.add_executive:
        existing = list(target.get("executives") or [])
        for ex in args.add_executive:
            if ex not in existing:
                existing.append(ex)
        diff["executives"] = (target.get("executives"), existing)
        target["executives"] = existing
    if args.add_company:
        existing = list(target.get("companies_owned") or [])
        for c in args.add_company:
            if c not in existing:
                existing.append(c)
        diff["companies_owned"] = (target.get("companies_owned"), existing)
        target["companies_owned"] = existing
    if args.add_brand:
        existing = list(target.get("brands_in_portfolio") or [])
        for b in args.add_brand:
            if b not in existing:
                existing.append(b)
        diff["brands_in_portfolio"] = (target.get("brands_in_portfolio"), existing)
        target["brands_in_portfolio"] = existing
    if args.note:
        prior = target.get("notes") or ""
        new_notes = (prior + "\n" + args.note).strip() if prior else args.note
        diff["notes"] = (prior, new_notes)
        target["notes"] = new_notes
    if not diff:
        print(f"No changes specified for {args.id}.", file=sys.stderr)
        return 1
    if args.dry_run:
        print(json.dumps({"id": args.id, "changes": diff}, indent=2, default=str))
        return 0
    snap = snapshot(core.STRATEGIC_OPERATORS_PATH, f"pre-operator-update-{args.id}-{_now_tag()}")
    _write_operators_file(data)
    if _validate_operators_or_rollback(snap) != 0:
        return 1
    print(f"Updated operator {args.id}: {', '.join(diff.keys())}.")
    return 0


def cmd_operator_record_movement(args) -> int:
    data, _ = _read_operators_file()
    operators = data.get("operators") or []
    target = next((op for op in operators if op.get("id") == args.operator_id), None)
    if target is None:
        print(f"ERROR: no operator with id={args.operator_id!r}.", file=sys.stderr)
        return 1
    event_at = args.event_at or date.today().isoformat()
    movement = {
        "id": args.id or _movement_id(event_at, args.summary),
        "event_at": event_at,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "movement_type": args.movement_type,
        "summary": args.summary,
        "source": args.source,
        "confidence": args.confidence,
    }
    if args.source_quality:
        movement["source_quality"] = args.source_quality
    if args.source_url:
        movement["source_url"] = args.source_url
    if args.affected_brands:
        movement["affected_brands"] = args.affected_brands
    if args.notes:
        movement["notes"] = args.notes
    if args.inference:
        inferences: dict[str, str] = {}
        for kv in args.inference:
            if "=" not in kv:
                print(f"ERROR: --inference must be key=level (got {kv!r}).", file=sys.stderr)
                return 1
            k, v = kv.split("=", 1)
            k, v = k.strip(), v.strip()
            if v not in {"low", "medium", "high", "critical"}:
                print(
                    f"ERROR: inference level must be low|medium|high|critical "
                    f"(got {v!r} for {k!r}).",
                    file=sys.stderr,
                )
                return 1
            inferences[k] = v
        if inferences:
            movement["inferences"] = inferences

    if args.dry_run:
        print(json.dumps({"operator_id": args.operator_id, "movement": movement},
                         indent=2, default=str))
        return 0

    movements = target.setdefault("movements", [])
    if any(m.get("id") == movement["id"] for m in movements):
        print(f"ERROR: movement id {movement['id']!r} already exists for {args.operator_id}.",
              file=sys.stderr)
        return 1
    movements.append(movement)
    target["last_movement_at"] = event_at
    snap = snapshot(core.STRATEGIC_OPERATORS_PATH, f"pre-operator-movement-{args.operator_id}-{_now_tag()}")
    _write_operators_file(data)
    if _validate_operators_or_rollback(snap) != 0:
        return 1
    print(f"Recorded {movement['id']} on operator {args.operator_id}.")
    return 0


def cmd_operator_close(args) -> int:
    data, _ = _read_operators_file()
    operators = data.get("operators") or []
    target = next((op for op in operators if op.get("id") == args.id), None)
    if target is None:
        print(f"ERROR: no operator with id={args.id!r}.", file=sys.stderr)
        return 1
    if target.get("status") == "closed":
        print(f"ERROR: operator {args.id!r} is already closed.", file=sys.stderr)
        return 1
    target["status"] = "closed"
    target["closed_at"] = args.closed_at or date.today().isoformat()
    if args.reason:
        target["closed_reason"] = args.reason
    if args.dry_run:
        print(json.dumps({"id": args.id, "status": "closed",
                          "closed_at": target["closed_at"],
                          "closed_reason": target.get("closed_reason")}, indent=2))
        return 0
    snap = snapshot(core.STRATEGIC_OPERATORS_PATH, f"pre-operator-close-{args.id}-{_now_tag()}")
    _write_operators_file(data)
    if _validate_operators_or_rollback(snap) != 0:
        return 1
    print(f"Closed operator {args.id}.")
    return 0


# -----------------------------------------------------------------------------
# Active-thread ops
# -----------------------------------------------------------------------------

def _read_threads_file() -> tuple[dict, str]:
    """Return (parsed dict, raw text)."""
    if not core.ACTIVE_THREADS_PATH.exists():
        return {"version": 1, "threads": []}, ""
    text = core.ACTIVE_THREADS_PATH.read_text()
    return core._yaml_load(text), text


def _write_threads_file(data: dict) -> None:
    try:
        import yaml  # type: ignore
    except ImportError:
        sys.stderr.write(
            "PyYAML required to write active_threads.yaml. "
            "Install with: pip install pyyaml --break-system-packages\n"
        )
        raise
    data["last_updated"] = date.today().isoformat()
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
    core.ACTIVE_THREADS_PATH.write_text(text)


def cmd_thread_open(args) -> int:
    data, _ = _read_threads_file()
    threads = data.setdefault("threads", [])
    if any(t.get("id") == args.id for t in threads):
        print(f"ERROR: thread {args.id!r} already exists.", file=sys.stderr)
        return 1
    thread = {
        "id": args.id,
        "title": args.title,
        "opened": date.today().isoformat(),
        "status": "open",
        "type": args.type,
        "people": args.people or [],
        "companies": args.companies or [],
        "context": args.context or "",
        "current_state": args.state or "",
        "boost_for_brief": args.boost_for_brief,
        "boost_score": args.boost_score,
    }
    if args.target_close:
        thread["target_close"] = args.target_close
    if args.dry_run:
        print(json.dumps(thread, indent=2))
        return 0
    threads.append(thread)
    snapshot(core.ACTIVE_THREADS_PATH, f"pre-thread-open-{args.id}-{_now_tag()}")
    _write_threads_file(data)
    print(f"Opened thread {args.id}.")
    return 0


def cmd_session_end(args) -> int:
    """Write a session memory file and refresh the session index."""
    import session_writer  # local import — script in same dir
    import session_index   # local import

    # Build summary dict from CLI args + optional JSON body
    body: dict = {}
    if args.body_file:
        body_text = Path(args.body_file).read_text()
        body_text = body_text.strip()
        if body_text.startswith("{"):
            body = json.loads(body_text)
        else:
            body = core._yaml_load(body_text) or {}

    summary = body if "session_id" in body else {
        "session_id": args.session_id,
        "date": args.date,
        "end_time": args.end_time,
        "duration_estimate": args.duration,
        "focus_areas": args.focus_areas or [],
        "threads_touched": args.threads_touched or [],
        "contacts_touched": args.contacts_touched or [],
        "mutations": json.loads(args.mutations) if args.mutations else {},
        "status": args.status,
        "next_session_should": args.next_session,
        "body": body,
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, default=str))
        return 0
    target = session_writer.write_session(summary)
    idx = session_index.build_index()
    session_index.INDEX_PATH.write_text(json.dumps(idx, indent=2) + "\n")
    print(f"Wrote {target.relative_to(core.PROJECT_DIR)}; "
          f"index has {idx['count']} session(s).")
    return 0


def cmd_my_post_add(args) -> int:
    """Append a single own-post to system/inbox/social.own_posts.json."""
    from datetime import datetime, timezone
    target = core.SOCIAL_OWN_POSTS_PATH
    if target.exists():
        try:
            data = json.loads(target.read_text())
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    data.setdefault("posts", [])
    data["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    post_id = args.id or args.url or f"own-{args.posted_at or 'unknown'}-{abs(hash(args.text)) % 100000}"
    new_post = {
        "post_id": post_id,
        "posted_at": args.posted_at,
        "platform": args.platform,
        "text": args.text,
        "topics": args.topics or [],
        "post_url": args.url,
        "engagement_totals": {
            "likes": args.likes, "comments": args.comments,
            "shares": args.shares, "impressions": args.impressions,
        },
    }
    if args.dry_run:
        print(json.dumps(new_post, indent=2, default=str))
        return 0
    posts = [p for p in data["posts"] if p.get("post_id") != post_id]
    posts.append(new_post)
    data["posts"] = posts
    core.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    if target.exists():
        snapshot(target, f"pre-mypost-add-{_now_tag()}")
    target.write_text(json.dumps(data, indent=2, default=str))
    print(f"Added own-post (id={post_id}).")
    return 0


def cmd_engagement_add(args) -> int:
    """Append a single engagement event to system/inbox/social.engagement.json."""
    from datetime import datetime, timezone
    target = core.SOCIAL_ENGAGEMENT_PATH
    if target.exists():
        try:
            data = json.loads(target.read_text())
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    data.setdefault("events", [])
    data["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    event = {
        "post_id": args.post_id,
        "type": args.type,
        "engager": {
            "name": args.engager_name,
            "linkedin_url": args.engager_url,
        },
        "at": args.at,
        "comment_text": args.comment_text,
    }
    if args.dry_run:
        print(json.dumps(event, indent=2, default=str))
        return 0
    data["events"].append(event)
    core.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    if target.exists():
        snapshot(target, f"pre-engagement-add-{_now_tag()}")
    target.write_text(json.dumps(data, indent=2, default=str))
    print(f"Added engagement: {args.type} by {args.engager_name} on post {args.post_id}.")
    return 0


def cmd_social_add(args) -> int:
    """Append a single social post to system/inbox/social.feed.json."""
    from datetime import datetime, timezone
    target = core.SOCIAL_FEED_PATH
    if target.exists():
        try:
            data = json.loads(target.read_text())
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    data.setdefault("posts", [])
    data["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data.setdefault("source", "manual_paste")

    post_id = args.id or (args.url or f"{args.author_name}::{args.posted_at or 'unknown'}")
    new_post = {
        "id": post_id,
        "author": {
            "name": args.author_name,
            "linkedin_url": args.author_url,
            "headline": args.author_headline,
        },
        "posted_at": args.posted_at,
        "text": args.text,
        "post_url": args.url,
        "platform": args.platform,
        "engagement": {
            "likes": args.likes,
            "comments": args.comments,
            "shares": args.shares,
        },
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "captured_via": "manual_paste",
    }
    if args.dry_run:
        print(json.dumps(new_post, indent=2, default=str))
        return 0
    # Dedup by id
    posts = [p for p in data["posts"] if p.get("id") != post_id]
    posts.append(new_post)
    data["posts"] = posts
    core.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    if target.exists():
        snapshot(target, f"pre-social-add-{_now_tag()}")
    target.write_text(json.dumps(data, indent=2, default=str))
    print(f"Added post by {args.author_name} (id={post_id}).")
    return 0


def cmd_thread_update(args) -> int:
    """Update mutable fields on an existing active_threads.yaml entry.

    RB-DEFECT-040/9.70 item 2 — closes two gaps:
      1. There was previously no CLI path to update an existing thread's
         `current_state`/`boost_score`/etc.; doing so required direct YAML
         edits (as done for T-2026-05-genius-global-payments on 2026-06-12).
      2. `active_threads.yaml` (career/business threads) and
         `system/tracked_opportunities.json` (read by
         `_active_opportunity_pipeline_items()` for the
         `active_opportunity_pipeline` brief section) are two separate
         stores with no sync. When `--opportunity-stage` is given on a
         thread whose `type` is in OPPORTUNITY_TYPES, this command also
         calls `opportunity_pipeline.process_opportunity_update(...,
         apply=True)` so both stores move together.
    """
    data, _ = _read_threads_file()
    threads = data.get("threads") or []
    thread = next((t for t in threads if t.get("id") == args.id), None)
    if thread is None:
        print(f"ERROR: thread {args.id!r} not found.", file=sys.stderr)
        return 1

    if args.current_state is not None:
        thread["current_state"] = args.current_state
    if args.status is not None:
        thread["status"] = args.status
    if args.boost_score is not None:
        thread["boost_score"] = args.boost_score
    if args.boost_for_brief is not None:
        thread["boost_for_brief"] = args.boost_for_brief

    opp_result = None
    OPPORTUNITY_TYPES = {"job_opportunity", "opportunity", "recruiter_engagement",
                         "partnership", "business_engagement"}
    if args.opportunity_stage:
        if thread.get("type") not in OPPORTUNITY_TYPES:
            print(
                f"ERROR: --opportunity-stage given but thread type "
                f"{thread.get('type')!r} is not in {sorted(OPPORTUNITY_TYPES)}.",
                file=sys.stderr,
            )
            return 2
        company = args.opportunity_company or (thread.get("companies") or [None])[0]
        if not company:
            print("ERROR: --opportunity-stage given but thread has no companies "
                  "and --opportunity-company was not provided.", file=sys.stderr)
            return 2
        opp_result = opportunity_pipeline.process_opportunity_update(
            text=args.current_state or thread.get("current_state") or "",
            company=company,
            role=args.opportunity_role,
            stage_override=args.opportunity_stage,
            source_type="thread_update",
            apply=not args.dry_run,
        )

    if args.dry_run:
        print(json.dumps(thread, indent=2, default=str))
        if opp_result is not None:
            print(json.dumps(opp_result, indent=2, default=str))
        return 0

    snapshot(core.ACTIVE_THREADS_PATH, f"pre-thread-update-{args.id}-{_now_tag()}")
    _write_threads_file(data)
    msg = f"Updated thread {args.id}."
    if opp_result is not None:
        msg += (
            f" Synced tracked_opportunities.json "
            f"(opportunity_id={opp_result['opportunity']['id']!r}, "
            f"stage={args.opportunity_stage!r}, "
            f"persistence_status={opp_result['persistence_status']!r})."
        )
    print(msg)
    return 0


def cmd_thread_close(args) -> int:
    data, _ = _read_threads_file()
    threads = data.get("threads") or []
    for t in threads:
        if t.get("id") == args.id:
            t["status"] = "closed"
            t["closed_date"] = date.today().isoformat()
            if args.reason:
                t["close_reason"] = args.reason
            if args.dry_run:
                print(json.dumps(t, indent=2, default=str))
                return 0
            snapshot(core.ACTIVE_THREADS_PATH, f"pre-thread-close-{args.id}-{_now_tag()}")
            _write_threads_file(data)
            print(f"Closed thread {args.id}.")
            return 0
    print(f"ERROR: thread {args.id!r} not found.", file=sys.stderr)
    return 1


# -----------------------------------------------------------------------------
# CLI wiring
# -----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    la = sub.add_parser("loop-add")
    la.add_argument("--party", required=True)
    la.add_argument("--description", required=True)
    la.add_argument("--target", required=True, help="ISO date YYYY-MM-DD")
    la.add_argument("--opened", help="ISO date (default: today)")
    la.add_argument("--id", help="Override loop id (default: next available)")
    la.add_argument("--dry-run", action="store_true")
    la.set_defaults(func=cmd_loop_add)

    lc = sub.add_parser("loop-close")
    lc.add_argument("--id", required=True, help="L-YYYY-MM-DD-NNN")
    lc.add_argument("--reason", required=True)
    lc.add_argument("--dry-run", action="store_true")
    lc.set_defaults(func=cmd_loop_close)

    lr = sub.add_parser("loop-redate", help="Update the target date on an open loop.")
    lr.add_argument("--id", required=True, help="L-YYYY-MM-DD-NNN")
    lr.add_argument("--target", required=True, help="New target date (YYYY-MM-DD)")
    lr.add_argument("--note", default=None, help="Optional note appended to description.")
    lr.add_argument("--dry-run", action="store_true")
    lr.set_defaults(func=cmd_loop_redate)

    t = sub.add_parser("touch")
    t.add_argument("--id", required=True, help="baseline entry id")
    t.add_argument("--date", help="ISO date (default: today)")
    t.add_argument("--source", help="Optional source tag to append to sources[].")
    t.add_argument("--dry-run", action="store_true")
    t.set_defaults(func=cmd_touch)

    ca = sub.add_parser("contact-add")
    ca.add_argument("--id", required=True)
    ca.add_argument("--name", required=True)
    ca.add_argument("--company", default=None, dest="company")
    ca.add_argument("--role", default=None)
    ca.add_argument("--linkedin", default=None)
    ca.add_argument("--email", default=None)
    ca.add_argument("--phone", default=None)
    ca.add_argument("--last-touch", dest="last_touch", default=None)
    ca.add_argument("--signal-class", dest="signal_class", required=True,
                    choices=["VC", "NPR", "LMI", "LKI", "RC"])
    ca.add_argument("--rc-tier", dest="rc_tier",
                    choices=["inner", "broader", "dormant_valuable"])
    ca.add_argument("--source", default=None)
    ca.add_argument("--notes", default=None)
    ca.add_argument("--dry-run", action="store_true")
    ca.set_defaults(func=cmd_contact_add)

    cu = sub.add_parser("contact-update",
                        help="Update mutable fields on an existing baseline contact.")
    cu.add_argument("--id", required=True, help="Contact id (e.g. amy-spytko)")
    cu.add_argument("--company", default=None)
    cu.add_argument("--role", default=None)
    cu.add_argument("--email", default=None)
    cu.add_argument("--phone", default=None)
    cu.add_argument("--linkedin", default=None)
    cu.add_argument("--last-touch", dest="last_touch", default=None)
    cu.add_argument("--signal-class", dest="signal_class", default=None,
                    choices=["VC", "NPR", "LMI", "LKI", "RC"])
    cu.add_argument("--rc-tier", dest="rc_tier", default=None,
                    choices=["inner", "broader", "dormant_valuable"])
    cu.add_argument("--rc-state", dest="rc_state", default=None,
                    choices=["ACTIVE", "INACTIVE", "DORMANT"])
    cu.add_argument("--relationship-domain", dest="relationship_domain", default=None,
                    choices=["family", "personal", "professional", "opportunity",
                             "industry", "vendor", "customer"])
    cu.add_argument("--notes", default=None,
                    help="Note to append (default) or replace (use --notes-replace).")
    cu.add_argument("--notes-replace", dest="notes_replace", action="store_true",
                    help="Replace notes entirely instead of appending.")
    cu.add_argument("--tags-add", dest="tags_add", nargs="*", default=None,
                    help="Tags to add (existing tags preserved).")
    cu.add_argument("--dry-run", action="store_true")
    cu.set_defaults(func=cmd_contact_update)

    ces = sub.add_parser("contact-set-employment-state",
                         help="Record a dated/operator-confirmed employment departure, clearing current_company/role while preserving history.")
    ces.add_argument("--id", required=True, help="Contact id (e.g. sal-nazir)")
    ces.add_argument("--employment-status", dest="employment_status", required=True,
                     choices=["stated_current_role", "no_stated_current_role",
                              "current_role_date_unavailable", "unknown"])
    ces.add_argument("--employment-status-source", dest="employment_status_source", required=True,
                     choices=["linkedin_profile_capture", "linkedin_connections_export", "operator_confirmed"])
    ces.add_argument("--employment-status-observed-at", dest="employment_status_observed_at", default=None)
    ces.add_argument("--employment-end-date", dest="employment_end_date", default=None)
    ces.add_argument("--employment-date-confidence", dest="employment_date_confidence", required=True,
                     choices=["dated", "undated", "operator_confirmed"])
    ces.add_argument("--last-known-company", dest="last_known_company", default=None)
    ces.add_argument("--last-known-role", dest="last_known_role", default=None)
    ces.add_argument("--dry-run", action="store_true")
    ces.set_defaults(func=cmd_contact_set_employment_state)

    bap = sub.add_parser("bulk-apply-pending",
                         help="Apply all pending LinkedIn-detected company/role changes in bulk.")
    bap.add_argument("--signal-class", dest="signal_class", default=None,
                     choices=["VC", "LMI", "LKI", "RC"],
                     help="Filter to a specific signal class (default: all).")
    bap.add_argument("--dry-run", action="store_true",
                     help="Show what would be applied without writing.")
    bap.set_defaults(func=cmd_bulk_apply_pending)

    to = sub.add_parser("thread-open")
    to.add_argument("--id", required=True)
    to.add_argument("--title", required=True)
    to.add_argument("--type", required=True,
                    choices=["job_opportunity", "business_engagement",
                             "partnership", "chapter_activation",
                             "role_search", "account_pursuit"])
    to.add_argument("--people", nargs="*", default=[])
    to.add_argument("--companies", nargs="*", default=[])
    to.add_argument("--context", default="")
    to.add_argument("--state", default="")
    to.add_argument("--target-close", default=None)
    to.add_argument("--boost-for-brief", dest="boost_for_brief",
                    default="medium", choices=["high", "medium", "low"])
    to.add_argument("--boost-score", dest="boost_score", type=float, default=1.2)
    to.add_argument("--dry-run", action="store_true")
    to.set_defaults(func=cmd_thread_open)

    se = sub.add_parser("session-end")
    se.add_argument("--session-id", dest="session_id",
                    help="YYYY-MM-DD-HHMM (default: now in CT).")
    se.add_argument("--date", help="ISO date (default: today).")
    se.add_argument("--end-time", dest="end_time")
    se.add_argument("--duration", help="Human-readable estimate, e.g. '~3 hours'.")
    se.add_argument("--focus-areas", dest="focus_areas", nargs="*")
    se.add_argument("--threads-touched", dest="threads_touched", nargs="*")
    se.add_argument("--contacts-touched", dest="contacts_touched", nargs="*")
    se.add_argument("--mutations",
                    help='JSON dict, e.g. \'{"contact_add": 1, "loop_close": 1}\'')
    se.add_argument("--status", default="complete",
                    choices=["complete", "partial", "aborted"])
    se.add_argument("--next-session", dest="next_session",
                    help="One-paragraph handoff to the next session.")
    se.add_argument("--body-file", dest="body_file",
                    help="JSON or YAML file containing the body sections. "
                         "If it includes 'session_id', it's treated as the full summary.")
    se.add_argument("--dry-run", action="store_true")
    se.set_defaults(func=cmd_session_end)

    mp = sub.add_parser("my-post-add")
    mp.add_argument("--text", required=True)
    mp.add_argument("--posted-at", dest="posted_at")
    mp.add_argument("--topics", nargs="*")
    mp.add_argument("--url")
    mp.add_argument("--platform", default="linkedin")
    mp.add_argument("--likes", type=int, default=None)
    mp.add_argument("--comments", type=int, default=None)
    mp.add_argument("--shares", type=int, default=None)
    mp.add_argument("--impressions", type=int, default=None)
    mp.add_argument("--id")
    mp.add_argument("--dry-run", action="store_true")
    mp.set_defaults(func=cmd_my_post_add)

    ea = sub.add_parser("engagement-add")
    ea.add_argument("--post-id", required=True, dest="post_id")
    ea.add_argument("--type", required=True, choices=["like", "comment", "share"])
    ea.add_argument("--engager-name", required=True, dest="engager_name")
    ea.add_argument("--engager-url", dest="engager_url")
    ea.add_argument("--at", help="ISO timestamp")
    ea.add_argument("--comment-text", dest="comment_text")
    ea.add_argument("--dry-run", action="store_true")
    ea.set_defaults(func=cmd_engagement_add)

    sa = sub.add_parser("social-add")
    sa.add_argument("--author-name", required=True, dest="author_name")
    sa.add_argument("--text", required=True,
                    help="The post body. Wrap in quotes; newlines OK.")
    sa.add_argument("--posted-at", dest="posted_at",
                    help="ISO date or 'YYYY-MM-DD' when the post was published.")
    sa.add_argument("--author-url", dest="author_url",
                    help="LinkedIn profile URL. Improves baseline matching.")
    sa.add_argument("--author-headline", dest="author_headline")
    sa.add_argument("--url", help="Post URL.")
    sa.add_argument("--platform", default="linkedin")
    sa.add_argument("--likes", type=int, default=None)
    sa.add_argument("--comments", type=int, default=None)
    sa.add_argument("--shares", type=int, default=None)
    sa.add_argument("--id", help="Override the post id (default: url or author::date).")
    sa.add_argument("--dry-run", action="store_true")
    sa.set_defaults(func=cmd_social_add)

    tu = sub.add_parser("thread-update",
                        help="Update mutable fields on an existing active thread; "
                             "optionally sync tracked_opportunities.json.")
    tu.add_argument("--id", required=True)
    tu.add_argument("--current-state", dest="current_state", default=None)
    tu.add_argument("--status", default=None)
    tu.add_argument("--boost-score", dest="boost_score", type=float, default=None)
    tu.add_argument("--boost-for-brief", dest="boost_for_brief", default=None,
                    choices=["high", "medium", "low"])
    tu.add_argument("--opportunity-stage", dest="opportunity_stage", default=None,
                    choices=opportunity_pipeline.STAGES,
                    help="If set (and thread type is an opportunity type), also "
                         "upsert system/tracked_opportunities.json with this stage.")
    tu.add_argument("--opportunity-company", dest="opportunity_company", default=None,
                    help="Company name for the tracked_opportunities.json entry "
                         "(default: thread's first companies[] entry).")
    tu.add_argument("--opportunity-role", dest="opportunity_role", default=None)
    tu.add_argument("--dry-run", action="store_true")
    tu.set_defaults(func=cmd_thread_update)

    tc = sub.add_parser("thread-close")
    tc.add_argument("--id", required=True)
    tc.add_argument("--reason", default=None)
    tc.add_argument("--dry-run", action="store_true")
    tc.set_defaults(func=cmd_thread_close)

    # Strategic-operator subcommands (RB 9.1)
    opa = sub.add_parser("operator-add")
    opa.add_argument("--id", required=True, help="Kebab-case slug.")
    opa.add_argument("--name", required=True)
    opa.add_argument("--entity-type", dest="entity_type", required=True,
                     choices=["multi_brand_operator", "pe_backed", "regional_scale",
                              "franchisee_group", "holding_co", "consolidator",
                              "expansion_leader"])
    opa.add_argument("--watchlist-bucket", dest="watchlist_bucket", required=True,
                     choices=["strategic_operator", "multi_brand_operator",
                              "expansion_leader", "high_influence_franchisee",
                              "pe_backed_groups"])
    opa.add_argument("--companies-owned", dest="companies_owned", nargs="*")
    opa.add_argument("--brand", dest="brands", nargs="*",
                     help="Kebab-case brand identifiers.")
    opa.add_argument("--executive", dest="executives", nargs="*",
                     help="Baseline contact_ids.")
    opa.add_argument("--relationship-proximity", dest="relationship_proximity",
                     choices=["direct", "one_hop", "two_hop", "none", "unknown"])
    opa.add_argument("--notes", default=None)
    opa.add_argument("--dry-run", action="store_true")
    opa.set_defaults(func=cmd_operator_add)

    opu = sub.add_parser("operator-update")
    opu.add_argument("--id", required=True)
    opu.add_argument("--name")
    opu.add_argument("--entity-type", dest="entity_type",
                     choices=["multi_brand_operator", "pe_backed", "regional_scale",
                              "franchisee_group", "holding_co", "consolidator",
                              "expansion_leader"])
    opu.add_argument("--watchlist-bucket", dest="watchlist_bucket",
                     choices=["strategic_operator", "multi_brand_operator",
                              "expansion_leader", "high_influence_franchisee",
                              "pe_backed_groups"])
    opu.add_argument("--relationship-proximity", dest="relationship_proximity",
                     choices=["direct", "one_hop", "two_hop", "none", "unknown"])
    opu.add_argument("--add-executive", dest="add_executive", nargs="*", default=None)
    opu.add_argument("--add-company", dest="add_company", nargs="*", default=None)
    opu.add_argument("--add-brand", dest="add_brand", nargs="*", default=None)
    opu.add_argument("--note", help="Appended to existing notes.")
    opu.add_argument("--dry-run", action="store_true")
    opu.set_defaults(func=cmd_operator_update)

    opm = sub.add_parser("operator-record-movement")
    opm.add_argument("--operator-id", dest="operator_id", required=True)
    opm.add_argument("--event-at", dest="event_at", help="ISO date (default: today).")
    opm.add_argument("--movement-type", dest="movement_type", required=True,
                     choices=["acquisition", "divestiture", "bankruptcy", "restructure",
                              "franchise_transfer", "pe_activity", "concept_expansion",
                              "geographic_expansion", "leadership_move",
                              "technology_standardization", "vendor_transition",
                              "labor_pressure", "unit_growth_velocity",
                              "integration_signal", "reporting_visibility_signal"])
    opm.add_argument("--summary", required=True)
    opm.add_argument("--source", required=True,
                     help="Source name (publication, primary doc, etc.).")
    opm.add_argument("--source-quality", dest="source_quality",
                     choices=["primary", "vertical_trade", "mainstream", "rumor"])
    opm.add_argument("--source-url", dest="source_url")
    opm.add_argument("--confidence", required=True,
                     choices=["low", "medium", "high", "critical"])
    opm.add_argument("--affected-brand", dest="affected_brands", nargs="*")
    opm.add_argument("--inference", action="append", default=None,
                     help="Repeatable: --inference key=level (e.g. operational_complexity_increase=high).")
    opm.add_argument("--notes")
    opm.add_argument("--id", help="Override the auto-generated movement id.")
    opm.add_argument("--dry-run", action="store_true")
    opm.set_defaults(func=cmd_operator_record_movement)

    opc = sub.add_parser("operator-close")
    opc.add_argument("--id", required=True)
    opc.add_argument("--reason")
    opc.add_argument("--closed-at", dest="closed_at",
                     help="ISO date (default: today).")
    opc.add_argument("--dry-run", action="store_true")
    opc.set_defaults(func=cmd_operator_close)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
