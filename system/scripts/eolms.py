#!/usr/bin/env python3
"""
eolms.py — Executive Open Loop Management System.

The primary interface for the ELoop register at system/eolms/loops.json —
strategic initiatives, projects, decisions, and waiting conditions, tracked
with a richer lifecycle than the flat open/closed loop_ledger.md model.
See system/design/EOLMS_SPEC.md for the full design.

All mutating commands snapshot loops.json before writing (once per day, into
system/eolms/archive/), default to preview-only, and require --confirm to
actually apply the change.

Usage:
    python3 eolms.py add --title "..." --category project --status active \\
        --confirm

    python3 eolms.py transition --id EL-2026-07-02-001 --to blocked \\
        --note "..." --confirm

    python3 eolms.py list --status active,waiting --category strategic_initiative
    python3 eolms.py status
    python3 eolms.py risks
    python3 eolms.py stale
    python3 eolms.py tick --confirm
    python3 eolms.py migrate --confirm
    python3 eolms.py validate
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core
import re

ELOOP_ID_RE = re.compile(r"^EL-\d{4}-\d{2}-\d{2}-(\d{3})$")


# -----------------------------------------------------------------------------
# Storage helpers
# -----------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _snapshot_if_needed() -> None:
    """Snapshot loops.json into archive/loops_YYYY-MM-DD.json, once per day."""
    if not core.EOLMS_PATH.exists():
        return
    core.EOLMS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dst = core.EOLMS_ARCHIVE_DIR / f"loops_{date.today().isoformat()}.json"
    if dst.exists():
        return
    shutil.copy2(core.EOLMS_PATH, dst)


def _load() -> list[core.ELoop]:
    # Pass EOLMS_PATH explicitly (rather than relying on load_eloops's default
    # argument, which is bound at function-definition time) so tests can
    # patch core.EOLMS_PATH to a tempfile and have it take effect here.
    return core.load_eloops(core.EOLMS_PATH)


def _save(loops: list[core.ELoop]) -> None:
    core.EOLMS_DIR.mkdir(parents=True, exist_ok=True)
    ordered = sorted(loops, key=lambda l: l.updated_at, reverse=True)
    core.EOLMS_PATH.write_text(
        json.dumps([l.to_dict() for l in ordered], indent=2), encoding="utf-8"
    )


def _next_id(loops: list[core.ELoop], today: date) -> str:
    prefix = f"EL-{today.isoformat()}-"
    used = [int(m.group(1)) for l in loops
            if (m := ELOOP_ID_RE.match(l.id)) and l.id.startswith(prefix)]
    n = (max(used) + 1) if used else 1
    return f"{prefix}{n:03d}"


def _find(loops: list[core.ELoop], loop_id: str) -> core.ELoop | None:
    return next((l for l in loops if l.id == loop_id), None)


def _by_id(loops: list[core.ELoop]) -> dict[str, core.ELoop]:
    return {l.id: l for l in loops}


# -----------------------------------------------------------------------------
# Lifecycle enforcement
# -----------------------------------------------------------------------------

def _open_blockers(loop: core.ELoop, index: dict[str, core.ELoop]) -> list[str]:
    """Return blocked_by ids that are not yet completed/archived (or unresolvable)."""
    open_ids = []
    for bid in loop.blocked_by:
        blocker = index.get(bid)
        if blocker is None or blocker.status not in core.ELOOP_TERMINAL_STATUSES:
            open_ids.append(bid)
    return open_ids


def _append_history(loop: core.ELoop, event: str, note: str | None = None,
                     frm: str | None = None, to: str | None = None) -> None:
    entry = {"ts": _now_iso(), "event": event}
    if frm:
        entry["from"] = frm
    if to:
        entry["to"] = to
    if note:
        entry["note"] = note
    loop.history.append(entry)


def _apply_transition(loop: core.ELoop, to_status: str, note: str | None,
                       index: dict[str, core.ELoop], *, event: str = "status_change",
                       force: bool = False, touch: bool = True) -> str | None:
    """Mutate loop in place. Returns an error string, or None on success."""
    if loop.is_terminal and not force:
        return f"{loop.id} is {loop.status} (terminal) — use --force to reopen."
    if to_status == "active" and not force:
        open_blockers = _open_blockers(loop, index)
        if open_blockers:
            return (f"{loop.id} cannot become active — blocked by "
                    f"{', '.join(open_blockers)}. Use --force to override, "
                    f"or set --to blocked instead.")
    frm = loop.status
    loop.status = to_status
    loop.updated_at = date.today()
    if touch:
        loop.last_activity = date.today()
    _append_history(loop, event, note=note, frm=frm, to=to_status)
    return None


# -----------------------------------------------------------------------------
# match_and_transition — narrated-evidence auto-closure
#
# The mechanism behind "the well pump is working" auto-closing the matching
# loop. Called from server.py's _execute_executive_declaration() so it fires
# on any surface that already routes CEO declarations there (ingestExecutive-
# Declaration, ingestContent's auto-persist path, capture processing).
#
# Safety is the whole point: a wrong auto-close is worse than no auto-close,
# so this only ever applies on an unambiguous single best match. Anything
# else — no match, or multiple similarly-plausible candidates — is reported
# back untouched.
# -----------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "for", "on", "in", "is", "are", "was", "were",
    "it", "its", "i", "we", "he", "she", "they", "with", "at", "by", "from", "that", "this",
    "has", "have", "had", "be", "been", "not", "just", "now", "today", "will", "would", "can",
    "up", "out", "me", "my", "our", "us", "so", "as", "if", "then", "than", "into",
}

_TOKEN_RE = re.compile(r"[a-z0-9']+")

# Checked in this order — an explicit resolution signal ("done", "fixed") wins even if the
# same sentence also contains advance-style language ("Ryan confirmed it's fixed").
_INTENT_PHRASES: list[tuple[str, tuple[str, ...]]] = [
    ("complete", ("working", "fixed", "resolved", "installed", "complete", "completed",
                  "done", "finished", "closed")),
    ("block", ("blocked", "stuck", "delayed", "paused", "pushed back", "pushed out")),
    ("advance", ("responded", "replied", "confirmed", "heard back", "got back",
                 "moved forward", "moving forward")),
]

MATCH_SCORE_FLOOR = 0.34
MATCH_MARGIN = 0.15


def _classify_intent(text: str) -> str | None:
    tl = text.lower()
    for intent, phrases in _INTENT_PHRASES:
        if any(p in tl for p in phrases):
            return intent
    return None


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2}


def _loop_distinctive_tokens(loop: core.ELoop) -> set[str]:
    """Proper-noun-ish identifiers: named people/orgs/tags. A hit here is a much stronger
    signal than a hit on a generic title verb ("send", "done", "complete")."""
    return _tokenize(" ".join(loop.related_people + loop.related_orgs + loop.tags))


def _loop_generic_tokens(loop: core.ELoop) -> set[str]:
    return _tokenize(" ".join([loop.title, loop.next_action or "", loop.waiting_on or ""]))


def _score(text_tokens: set[str], loop: core.ELoop) -> float:
    """Weighted overlap score. Distinctive-token hits (name/org/tag) count double and are
    required whenever the loop has any — a declaration that only echoes generic verbs from
    the title ("done", "sent", "complete") without naming who/what it's about shouldn't be
    enough to auto-close something. Loops with no distinctive identifiers at all fall back
    to a stricter plain-overlap check on title/next_action/waiting_on text.
    """
    distinctive = _loop_distinctive_tokens(loop)
    generic = _loop_generic_tokens(loop) - distinctive

    if distinctive:
        distinctive_hits = text_tokens & distinctive
        if not distinctive_hits:
            return 0.0
        generic_hits = text_tokens & generic
        weighted_overlap = 2 * len(distinctive_hits) + len(generic_hits)
        weighted_total = 2 * len(distinctive) + len(generic)
        return weighted_overlap / weighted_total if weighted_total else 0.0

    # No name/org/tag to anchor on — require a higher bar on plain overlap.
    if not generic:
        return 0.0
    overlap = text_tokens & generic
    if not overlap:
        return 0.0
    return 1.4 * (len(overlap) / min(len(text_tokens), len(generic)))


def _target_status_for_intent(intent: str, loop: core.ELoop) -> str | None:
    if intent == "complete":
        # RB-DEFECT-060 follow-up: loops flagged as needing independent verification
        # (defect-fix-style closures) land in pending_verification, not completed —
        # "the fix is in" and "it's confirmed working" are different facts.
        return "pending_verification" if loop.requires_verification else "completed"
    if intent == "block":
        return None if loop.status == "blocked" else "blocked"
    if intent == "advance":
        if loop.status in ("waiting", "deferred", "identified"):
            return "active"
        return None
    return None


def close_by_id(loop_id: str, reason: str) -> str | None:
    """Close an EOLMS loop directly by its own EL- id. Returns an error
    string, or None on success.

    RB-2026-08-24: closeLoop (mutations.cmd_loop_close, /loops/close) only
    ever wrote to loop_ledger.md -- an EL- id passed to it simply isn't
    present in that file's text, so it failed with "id not found". Discovered
    live when RBB (correctly) gave an EL- id for an EOLMS-only loop that was
    never migrated into loop_ledger.md. This is the direct-id counterpart to
    close_by_ledger_id below (which closes the EOLMS side when a *ledger* id
    was closed) -- same explicit-id-plus-reason safety profile as the
    existing loop_ledger.md close path, not the fuzzy-match machinery in
    match_and_transition.
    """
    loops = _load()
    loop = _find(loops, loop_id)
    if loop is None:
        return f"{loop_id} not found"
    index = _by_id(loops)
    err = _apply_transition(loop, "completed", reason, index, event="status_change")
    if err:
        return err
    _snapshot_if_needed()
    _save(loops)
    return None


def close_by_ledger_id(ledger_id: str, reason: str) -> dict | None:
    """Transition the EOLMS entry tracking a given loop_ledger.md id to
    'completed', if one exists. Returns the closed entry's dict, or None if
    no EOLMS entry references this ledger id (not an error — many
    loop_ledger loops were never migrated into EOLMS).

    RB-2026-07-15: `mutations.cmd_loop_close` (the actual `closeLoop` API
    handler) only ever wrote to loop_ledger.md — never to eolms/loops.json,
    even though `getRenderedDailyBrief`'s Executive Status view reads EOLMS,
    not the ledger. A loop could be closed correctly via closeLoop and still
    render as open in the brief. Call this right after a successful
    loop_ledger.md close so the two stores can't diverge again.
    """
    loops = _load()
    target = next(
        (l for l in loops
         if ledger_id in (l.related_loop_ids or [])
         or ledger_id in (l.source_ref or "")),
        None,
    )
    if target is None:
        return None
    _snapshot_if_needed()
    index = _by_id(loops)
    err = _apply_transition(
        target, "completed", f"Closed via closeLoop ({ledger_id}): {reason}",
        index, event="status_change",
    )
    if err:
        return None
    _save(loops)
    return target.to_dict()


def match_and_transition(text: str, *, apply: bool = False) -> dict:
    """Match narrated evidence text against open loops; transition on a confident match.

    Returns one of:
      {"status": "no_match", ...}          — no completion/advance/block language, or no loop cleared the floor
      {"status": "ambiguous", "candidates": [...]}  — multiple similarly-plausible loops; not applied
      {"status": "blocked_by_dependency", ...}      — best match found but a dependency gate refused it
      {"status": "dry_run" | "applied", "loop_id", "to_status", "score"}
    """
    intent = _classify_intent(text)
    if intent is None:
        return {"status": "no_match", "reason": "no completion/advance/block language detected"}

    text_tokens = _tokenize(text)
    if not text_tokens:
        return {"status": "no_match", "intent": intent, "reason": "no matchable tokens in text"}

    loops = _load()
    scored: list[tuple[core.ELoop, float, str]] = []
    for l in loops:
        if l.status in core.ELOOP_TERMINAL_STATUSES:
            continue
        target = _target_status_for_intent(intent, l)
        if target is None:
            continue
        score = _score(text_tokens, l)
        if score > 0:
            scored.append((l, score, target))

    if not scored:
        return {"status": "no_match", "intent": intent}

    scored.sort(key=lambda x: -x[1])
    top_loop, top_score, top_target = scored[0]
    if top_score < MATCH_SCORE_FLOOR:
        return {"status": "no_match", "intent": intent, "best_score": round(top_score, 3)}

    second_score = scored[1][1] if len(scored) > 1 else 0.0
    if len(scored) > 1 and (top_score - second_score) < MATCH_MARGIN:
        return {
            "status": "ambiguous", "intent": intent,
            "candidates": [{"id": l.id, "title": l.title, "score": round(s, 3)} for l, s, _t in scored[:5]],
        }

    index = _by_id(loops)
    err = _apply_transition(
        top_loop, top_target,
        f'Auto-transition from narrated evidence: "{text.strip()[:200]}"',
        index, event="intelligence_signal", force=False,
    )
    if err:
        return {"status": "blocked_by_dependency", "loop_id": top_loop.id, "detail": err}

    if not apply:
        return {"status": "dry_run", "loop_id": top_loop.id, "title": top_loop.title,
                 "to_status": top_target, "score": round(top_score, 3)}

    _snapshot_if_needed()
    _save(loops)
    return {"status": "applied", "loop_id": top_loop.id, "title": top_loop.title,
             "to_status": top_target, "score": round(top_score, 3)}


def cmd_match(args) -> int:
    result = match_and_transition(args.text, apply=args.confirm)
    print(json.dumps(result, indent=2))
    if result.get("status") == "dry_run":
        print("\nDRY RUN — not written. Re-run with --confirm to apply.", file=sys.stderr)
    elif result.get("status") == "applied":
        print(f"\nApplied: {result['loop_id']} -> {result['to_status']}.", file=sys.stderr)
    return 0


# -----------------------------------------------------------------------------
# add / update / transition / get / list
# -----------------------------------------------------------------------------

def cmd_add(args) -> int:
    loops = _load()
    today = date.today()
    loop_id = args.id or _next_id(loops, today)
    loop = core.ELoop(
        id=loop_id, title=args.title, category=args.category,
        status=args.status or "identified", priority=args.priority or "medium",
        strategic_value=args.strategic_value, owner=args.owner or "Todd Vahlsing",
        created_at=today, updated_at=today, last_activity=today,
        next_action=args.next_action, waiting_on=args.waiting_on,
        activation_date=date.fromisoformat(args.activation_date) if args.activation_date else None,
        activation_condition=args.activation_condition,
        due_date=date.fromisoformat(args.due) if args.due else None,
        cadence_days=args.cadence_days,
        related_people=args.people or [], related_orgs=args.orgs or [],
        related_loop_ids=args.related_loops or [], blocked_by=args.blocked_by or [],
        source_ref=args.source_ref or "manual", confidence=args.confidence or "medium",
        tags=args.tags or [], requires_verification=bool(args.requires_verification),
    )
    _append_history(loop, "created", note=args.note)

    if loop.status not in core.ELOOP_STATUSES:
        print(f"error: unknown status '{loop.status}'", file=sys.stderr)
        return 2
    if loop.category not in core.ELOOP_CATEGORIES:
        print(f"error: unknown category '{loop.category}'", file=sys.stderr)
        return 2

    if loop.status == "active" and loop.blocked_by and not args.force:
        index = _by_id(loops)
        open_blockers = _open_blockers(loop, index)
        if open_blockers:
            print(f"error: {loop_id} cannot start active — blocked by "
                  f"{', '.join(open_blockers)}. Pass --status blocked or --force.", file=sys.stderr)
            return 1

    print(json.dumps(loop.to_dict(), indent=2))
    if not args.confirm:
        print("\nDRY RUN — not written. Re-run with --confirm to apply.", file=sys.stderr)
        return 0
    _snapshot_if_needed()
    loops.append(loop)
    _save(loops)
    print(f"\nAdded {loop_id}.", file=sys.stderr)
    return 0


def cmd_update(args) -> int:
    loops = _load()
    loop = _find(loops, args.id)
    if loop is None:
        print(f"error: {args.id} not found", file=sys.stderr)
        return 1

    changed = False
    for field_name, cli_val in (
        ("next_action", args.next_action), ("waiting_on", args.waiting_on),
        ("activation_condition", args.activation_condition),
        ("strategic_value", args.strategic_value), ("priority", args.priority),
        ("confidence", args.confidence),
    ):
        if cli_val is not None:
            setattr(loop, field_name, cli_val)
            changed = True
    if args.activation_date is not None:
        loop.activation_date = date.fromisoformat(args.activation_date)
        changed = True
    if args.due is not None:
        loop.due_date = date.fromisoformat(args.due)
        changed = True
    if args.cadence_days is not None:
        loop.cadence_days = args.cadence_days
        changed = True
    if args.add_blocked_by:
        loop.blocked_by = sorted(set(loop.blocked_by) | set(args.add_blocked_by))
        changed = True
    if args.remove_blocked_by:
        loop.blocked_by = [b for b in loop.blocked_by if b not in set(args.remove_blocked_by)]
        changed = True
    if args.add_tag:
        loop.tags = sorted(set(loop.tags) | set(args.add_tag))
        changed = True
    if args.requires_verification is not None:
        loop.requires_verification = args.requires_verification == "yes"
        changed = True

    err = None
    if args.status:
        index = _by_id(loops)
        err = _apply_transition(loop, args.status, args.note, index, force=args.force)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        changed = True
    elif changed:
        loop.updated_at = date.today()
        if args.touch:
            loop.last_activity = date.today()
        _append_history(loop, "field_update", note=args.note)

    if not changed and not args.note:
        print("nothing to update — pass at least one field or --note", file=sys.stderr)
        return 2
    if args.note and not changed:
        _append_history(loop, "note", note=args.note)

    print(json.dumps(loop.to_dict(), indent=2))
    if not args.confirm:
        print("\nDRY RUN — not written. Re-run with --confirm to apply.", file=sys.stderr)
        return 0
    _snapshot_if_needed()
    _save(loops)
    print(f"\nUpdated {loop.id}.", file=sys.stderr)
    return 0


def cmd_transition(args) -> int:
    loops = _load()
    loop = _find(loops, args.id)
    if loop is None:
        print(f"error: {args.id} not found", file=sys.stderr)
        return 1
    if args.to not in core.ELOOP_STATUSES:
        print(f"error: unknown status '{args.to}'", file=sys.stderr)
        return 2
    index = _by_id(loops)
    err = _apply_transition(loop, args.to, args.note, index, force=args.force)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(json.dumps(loop.to_dict(), indent=2))
    if not args.confirm:
        print("\nDRY RUN — not written. Re-run with --confirm to apply.", file=sys.stderr)
        return 0
    _snapshot_if_needed()
    _save(loops)
    print(f"\nTransitioned {loop.id} -> {args.to}.", file=sys.stderr)
    return 0


def cmd_get(args) -> int:
    loops = _load()
    loop = _find(loops, args.id)
    if loop is None:
        print(f"error: {args.id} not found", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(loop.to_dict(), indent=2))
    else:
        d = loop.to_dict()
        for k, v in d.items():
            if k == "history":
                continue
            print(f"{k}: {v}")
        print("history:")
        for h in loop.history:
            line = f"  - {h.get('ts', '')[:19]} {h['event']}"
            if h.get("from") or h.get("to"):
                line += f" ({h.get('from', '?')} -> {h.get('to', '?')})"
            if h.get("note"):
                line += f" — {h['note']}"
            print(line)
    return 0


def _matches_filters(loop: core.ELoop, args) -> bool:
    if args.status and loop.status not in args.status.split(","):
        return False
    if args.category and loop.category not in args.category.split(","):
        return False
    if args.priority and loop.priority not in args.priority.split(","):
        return False
    if args.blocked and loop.status != "blocked":
        return False
    if args.stale:
        threshold = loop.dormancy_threshold()
        if loop.status not in ("active", "waiting") or threshold is None:
            return False
        if loop.days_since_activity <= core.EOLMS_STALENESS_WARNING_DAYS:
            return False
    if args.changed_since:
        since = date.fromisoformat(args.changed_since)
        touched = loop.updated_at >= since or any(
            (h.get("ts") or "")[:10] >= args.changed_since for h in loop.history
        )
        if not touched:
            return False
    return True


def cmd_list(args) -> int:
    loops = [l for l in _load() if _matches_filters(l, args)]
    loops.sort(key=lambda l: (l.priority != "critical", l.priority != "high", l.updated_at), reverse=False)
    if args.json:
        print(json.dumps([l.to_dict() for l in loops], indent=2))
        return 0
    if not loops:
        print("(no matching loops)")
        return 0
    for l in loops:
        blocked_note = f" [blocked_by: {', '.join(l.blocked_by)}]" if l.blocked_by else ""
        due = f" due {l.due_date.isoformat()}" if l.due_date else ""
        age = f" — {l.days_since_activity}d since activity"
        print(f"{l.id}  [{l.status:9s}] [{l.priority:8s}] {l.title}{due}{age}{blocked_note}")
    return 0


# -----------------------------------------------------------------------------
# Executive roll-ups
# -----------------------------------------------------------------------------

def cmd_status(args) -> int:
    loops = _load()
    summary = core.eloops_executive_summary(loops)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    c = summary["counts"]
    print("## Executive Status\n")
    print(f"Active Strategic Initiatives: {c['active_strategic_initiatives']}")
    print(f"Active Projects: {c['active_projects']}")
    print(f"Decisions Needed: {c['decisions_needed']}")
    waiting_note = f"  ({c['waiting_items_overdue']} overdue for follow-up)" if c['waiting_items_overdue'] else ""
    print(f"Waiting Items: {c['waiting_items']}{waiting_note}")
    print(f"Relationship Follow-ups Due: {c['relationship_followups_due']}")
    print(f"Blocked: {c['blocked']}")
    print(f"Completed Since Yesterday: {c['completed_since_yesterday']}")
    print(f"Dormant: {c['dormant']}")
    print(f"Stale (active, no update in {core.EOLMS_STALENESS_WARNING_DAYS}+ days): {c['stale_active']}")
    if summary["recommendation_candidates"]:
        print(f"\nCoS Recommendation: " + "; ".join(summary["recommendation_candidates"]) + ".")
    return 0


def cmd_risks(args) -> int:
    loops = _load()
    at_risk_statuses = {"blocked", "waiting", "dormant", "deferred"}
    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "monitor": 4}
    risky = [l for l in loops if l.status in at_risk_statuses and l.priority in ("critical", "high")]
    risky.sort(key=lambda l: (priority_rank.get(l.priority, 9), -l.days_since_activity))
    top = risky[: args.limit]
    if args.json:
        print(json.dumps([l.to_dict() for l in top], indent=2))
        return 0
    if not top:
        print("No high-priority loops currently at risk.")
        return 0
    print(f"## Top {len(top)} Strategic Risks\n")
    for l in top:
        blocker_note = f" — blocked by {', '.join(l.blocked_by)}" if l.blocked_by else ""
        waiting_note = f" — waiting on: {l.waiting_on}" if l.waiting_on else ""
        print(f"- **{l.title}** [{l.priority}/{l.status}] — {l.days_since_activity}d since activity{blocker_note}{waiting_note}")
    return 0


def cmd_stale(args) -> int:
    loops = _load()
    stale = []
    for l in loops:
        threshold = l.dormancy_threshold()
        if l.status not in ("active", "waiting") or threshold is None:
            continue
        if l.days_since_activity > core.EOLMS_STALENESS_WARNING_DAYS:
            stale.append((l, threshold))
    stale.sort(key=lambda pair: -pair[0].days_since_activity)
    if args.json:
        print(json.dumps([l.to_dict() for l, _ in stale], indent=2))
        return 0
    if not stale:
        print("No stale loops.")
        return 0
    for l, threshold in stale:
        flag = " — PAST DORMANCY THRESHOLD" if l.days_since_activity > threshold else ""
        print(f"{l.id}  {l.title} — {l.days_since_activity}d since activity (threshold {threshold}d){flag}")
    return 0


# -----------------------------------------------------------------------------
# tick — the automation engine
# -----------------------------------------------------------------------------

def _tick(loops: list[core.ELoop]) -> list[str]:
    """Apply dependency resolution/enforcement, trigger activation, and
    dormancy transitions in place. Returns a list of human-readable change lines."""
    today = date.today()
    changes: list[str] = []

    # 1. Dependency resolution: blocked -> waiting once all blockers are done.
    index = _by_id(loops)
    for l in loops:
        if l.status != "blocked" or not l.blocked_by:
            continue
        if not _open_blockers(l, index):
            frm = l.status
            l.status = "waiting"
            l.updated_at = today
            l.last_activity = today
            _append_history(l, "auto_transition", note="dependency cleared — ready to resume", frm=frm, to="waiting")
            changes.append(f"{l.id}: blocked -> waiting (dependencies cleared)")

    # 2. Dependency enforcement: active/waiting -> blocked if a blocker reopened.
    index = _by_id(loops)  # re-index after step 1 status changes
    for l in loops:
        if l.status not in ("active", "waiting") or not l.blocked_by:
            continue
        open_blockers = _open_blockers(l, index)
        if open_blockers:
            frm = l.status
            l.status = "blocked"
            l.updated_at = today
            l.last_activity = today
            _append_history(l, "auto_transition",
                             note=f"blocked by open dependency: {', '.join(open_blockers)}",
                             frm=frm, to="blocked")
            changes.append(f"{l.id}: {frm} -> blocked (dependency {', '.join(open_blockers)} still open)")

    # 3. Trigger activation: deferred/identified with activation_date due -> active.
    for l in loops:
        if l.status not in ("deferred", "identified") or not l.activation_date:
            continue
        if l.activation_date <= today:
            frm = l.status
            l.status = "active"
            l.updated_at = today
            l.last_activity = today
            _append_history(l, "auto_transition",
                             note=f"activated by trigger date {l.activation_date.isoformat()}",
                             frm=frm, to="active")
            changes.append(f"{l.id}: {frm} -> active (trigger date {l.activation_date.isoformat()} reached)")

    # 4. Dormancy: active past category threshold -> dormant.
    for l in loops:
        if l.status != "active":
            continue
        threshold = l.dormancy_threshold()
        if threshold is None:
            continue
        if l.days_since_activity > threshold:
            frm = l.status
            l.status = "dormant"
            l.updated_at = today
            _append_history(l, "auto_transition",
                             note=f"no activity in {l.days_since_activity}d (threshold {threshold}d)",
                             frm=frm, to="dormant")
            changes.append(f"{l.id}: active -> dormant ({l.days_since_activity}d since activity, threshold {threshold}d)")

    return changes


def cmd_tick(args) -> int:
    loops = _load()
    changes = _tick(loops)
    if not changes:
        print("No transitions.")
        return 0
    for c in changes:
        print(c)
    if not args.confirm:
        print("\nDRY RUN — not written. Re-run with --confirm to apply.", file=sys.stderr)
        return 0
    _snapshot_if_needed()
    _save(loops)
    print(f"\nApplied {len(changes)} transition(s).", file=sys.stderr)
    return 0


# -----------------------------------------------------------------------------
# migrate — import loop_ledger.md
# -----------------------------------------------------------------------------

_CATEGORY_KEYWORDS = [
    (("introduce", "intro"), "action"),
    (("reach out", "re-engage", "reconnect", "follow up", "follow-up", "check-in", "check in", "text"), "action"),
    (("monitor", "watch-list", "watch list", "stay close", "track"), "waiting"),
    (("define", "draft", "prepare", "outline", "build"), "project"),
    (("discuss", "schedule", "call", "conversation"), "action"),
]


def _infer_category(desc: str) -> str:
    dl = desc.lower()
    for keywords, cat in _CATEGORY_KEYWORDS:
        if any(k in dl for k in keywords):
            return cat
    return "action"


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _last_activity_from_description(desc: str, opened: date) -> date:
    dates = [date.fromisoformat(m) for m in _DATE_RE.findall(desc)]
    dates.append(opened)
    today = date.today()
    return max(d for d in dates if d <= today) if any(d <= today for d in dates) else opened


def cmd_migrate(args) -> int:
    ledger_loops = core.parse_loop_ledger()
    existing = _load()
    already_migrated = {l.source_ref for l in existing if l.source_ref}

    new_eloops: list[core.ELoop] = []
    for L in ledger_loops:
        source_ref = f"loop_ledger:{L.id}"
        if source_ref in already_migrated:
            continue
        title = L.description.split("[")[0].strip().split(". ")[0][:140].strip() or L.description[:140]
        status = "completed" if L.closed else "active"
        category = _infer_category(L.description)
        last_activity = _last_activity_from_description(L.description, L.opened)
        loop = core.ELoop(
            id=_next_id(existing + new_eloops, L.opened),
            title=title, category=category, status=status, priority="medium",
            created_at=L.opened, updated_at=last_activity, last_activity=last_activity,
            next_action=L.description[:280].strip(),
            waiting_on=L.description[:200].strip() if category == "waiting" and status != "completed" else None,
            due_date=L.target,
            related_people=[L.party], related_loop_ids=[L.id],
            source_ref=source_ref, confidence="high",
        )
        _append_history(loop, "migrated", note=f"Migrated from {L.id}: {L.description}")
        new_eloops.append(loop)

    if not new_eloops:
        print("Nothing to migrate — all loop_ledger.md rows already imported.")
        return 0

    print(f"{len(new_eloops)} loop(s) to migrate:")
    for l in new_eloops:
        print(f"  {l.id}  [{l.status}/{l.category}] {l.title}")

    if not args.confirm:
        print("\nDRY RUN — not written. Re-run with --confirm to apply.", file=sys.stderr)
        return 0
    _snapshot_if_needed()
    _save(existing + new_eloops)
    print(f"\nMigrated {len(new_eloops)} loop(s).", file=sys.stderr)
    return 0


# -----------------------------------------------------------------------------
# validate
# -----------------------------------------------------------------------------

def cmd_validate(args) -> int:
    if not core.EOLMS_PATH.exists():
        print("No loops.json yet — nothing to validate.")
        return 0
    try:
        raw = json.loads(core.EOLMS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: loops.json is not valid JSON: {e}", file=sys.stderr)
        return 1

    try:
        import jsonschema
        schema = json.loads(core.EOLMS_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(raw, schema)
        print(f"OK — {len(raw)} loop(s) valid against schema (jsonschema).")
        return 0
    except ImportError:
        pass
    except Exception as e:
        print(f"error: schema validation failed: {e}", file=sys.stderr)
        return 1

    # Fallback manual validation if jsonschema isn't installed.
    errors = []
    ids_seen = set()
    for i, d in enumerate(raw):
        for req in ("id", "title", "category", "status", "priority", "owner",
                    "created_at", "updated_at", "last_activity", "confidence", "history"):
            if req not in d:
                errors.append(f"[{i}] missing required field '{req}'")
        if d.get("category") not in core.ELOOP_CATEGORIES:
            errors.append(f"[{i}] {d.get('id')}: bad category '{d.get('category')}'")
        if d.get("status") not in core.ELOOP_STATUSES:
            errors.append(f"[{i}] {d.get('id')}: bad status '{d.get('status')}'")
        if d.get("id") in ids_seen:
            errors.append(f"[{i}] duplicate id '{d.get('id')}'")
        ids_seen.add(d.get("id"))
    if errors:
        print(f"{len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"OK — {len(raw)} loop(s) valid (manual check; install `jsonschema` for full schema validation).")
    return 0


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Executive Open Loop Management System.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="Create a new loop.")
    a.add_argument("--id")
    a.add_argument("--title", required=True)
    a.add_argument("--category", required=True, choices=core.ELOOP_CATEGORIES)
    a.add_argument("--status", choices=core.ELOOP_STATUSES)
    a.add_argument("--priority", choices=core.ELOOP_PRIORITIES)
    a.add_argument("--strategic-value", dest="strategic_value")
    a.add_argument("--owner")
    a.add_argument("--next-action", dest="next_action")
    a.add_argument("--waiting-on", dest="waiting_on")
    a.add_argument("--activation-date", dest="activation_date")
    a.add_argument("--activation-condition", dest="activation_condition")
    a.add_argument("--due")
    a.add_argument("--cadence-days", dest="cadence_days", type=int)
    a.add_argument("--people", nargs="*")
    a.add_argument("--orgs", nargs="*")
    a.add_argument("--related-loops", dest="related_loops", nargs="*")
    a.add_argument("--blocked-by", dest="blocked_by", nargs="*")
    a.add_argument("--source-ref", dest="source_ref")
    a.add_argument("--confidence", choices=core.ELOOP_CONFIDENCE)
    a.add_argument("--tags", nargs="*")
    a.add_argument("--requires-verification", dest="requires_verification", action="store_true",
                    help="A 'complete' match lands in pending_verification instead of completed.")
    a.add_argument("--note")
    a.add_argument("--force", action="store_true")
    a.add_argument("--confirm", action="store_true")
    a.set_defaults(func=cmd_add)

    u = sub.add_parser("update", help="Update fields on an existing loop.")
    u.add_argument("--id", required=True)
    u.add_argument("--status", choices=core.ELOOP_STATUSES)
    u.add_argument("--next-action", dest="next_action")
    u.add_argument("--waiting-on", dest="waiting_on")
    u.add_argument("--activation-date", dest="activation_date")
    u.add_argument("--activation-condition", dest="activation_condition")
    u.add_argument("--strategic-value", dest="strategic_value")
    u.add_argument("--priority", choices=core.ELOOP_PRIORITIES)
    u.add_argument("--confidence", choices=core.ELOOP_CONFIDENCE)
    u.add_argument("--due")
    u.add_argument("--cadence-days", dest="cadence_days", type=int)
    u.add_argument("--add-blocked-by", dest="add_blocked_by", nargs="*")
    u.add_argument("--remove-blocked-by", dest="remove_blocked_by", nargs="*")
    u.add_argument("--add-tag", dest="add_tag", nargs="*")
    u.add_argument("--requires-verification", dest="requires_verification", choices=("yes", "no"),
                    help="Flip whether a future 'complete' match lands in pending_verification.")
    u.add_argument("--note")
    u.add_argument("--touch", action="store_true", default=True)
    u.add_argument("--force", action="store_true")
    u.add_argument("--confirm", action="store_true")
    u.set_defaults(func=cmd_update)

    t = sub.add_parser("transition", help="Change a loop's status.")
    t.add_argument("--id", required=True)
    t.add_argument("--to", required=True, choices=core.ELOOP_STATUSES)
    t.add_argument("--note")
    t.add_argument("--force", action="store_true")
    t.add_argument("--confirm", action="store_true")
    t.set_defaults(func=cmd_transition)

    g = sub.add_parser("get", help="Show one loop.")
    g.add_argument("--id", required=True)
    g.add_argument("--json", action="store_true")
    g.set_defaults(func=cmd_get)

    l = sub.add_parser("list", help="List loops, optionally filtered.")
    l.add_argument("--status")
    l.add_argument("--category")
    l.add_argument("--priority")
    l.add_argument("--blocked", action="store_true")
    l.add_argument("--stale", action="store_true")
    l.add_argument("--changed-since", dest="changed_since")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("status", help="Executive roll-up summary + CoS recommendation.")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    r = sub.add_parser("risks", help="Top strategic risks.")
    r.add_argument("--limit", type=int, default=5)
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_risks)

    st = sub.add_parser("stale", help="Loops past staleness warning threshold.")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_stale)

    tk = sub.add_parser("tick", help="Run the automation engine (dependencies, triggers, dormancy).")
    tk.add_argument("--confirm", action="store_true")
    tk.set_defaults(func=cmd_tick)

    m = sub.add_parser("migrate", help="Import loop_ledger.md rows not yet in EOLMS.")
    m.add_argument("--confirm", action="store_true")
    m.set_defaults(func=cmd_migrate)

    v = sub.add_parser("validate", help="Validate loops.json against the schema.")
    v.set_defaults(func=cmd_validate)

    mt = sub.add_parser("match", help="Match narrated evidence text against open loops; transition on a confident match.")
    mt.add_argument("--text", required=True)
    mt.add_argument("--confirm", action="store_true")
    mt.set_defaults(func=cmd_match)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
