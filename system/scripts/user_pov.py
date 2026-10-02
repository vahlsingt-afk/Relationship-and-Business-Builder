"""
user_pov.py — governed storage for the User POV Registry
(system/POV_REGISTRY_FEATURE_BRIEF_2026-10-01.md).

Phase 1: atomic registry + evidence links. Named `user_pov` (not
`operator_pov` or `todds_pov`) because this is a general RBB capability,
not Todd-specific — see the feature brief's "Naming" section. Todd is
simply the first user whose POV populates it.

Real gap this closes (CANONICAL_REGISTRY.yaml's `strategic_theses` domain
already flagged it: authority_status distributed_consolidation_pending,
authoritative_store null): system/00_TODD_PROFILE.md mixed biography,
preferences, strategic theses, and hard operating boundaries in one
static, unversioned file with no way to track when a belief changed, why,
or what evidence supports/challenges it. Investigated before building:
strategic_events.json/.py (listed as a `strategic_theses` current_store)
turned out to be a genuinely different concept -- RB's market/industry
EVENT convergence log, not a registry of Todd's own beliefs -- and a grep
across active_threads.yaml/account_intelligence//research/ found only
scattered incidental mentions of the word "thesis", not a second
concentrated source. 00_TODD_PROFILE.md's own "Strategic theses" and
"BridgePoint Ops engagement boundaries" sections are the real
concentrated content (see migrate_todd_profile_pov.py).

Event-sourced (events.jsonl is the immutable history; registry.json is
the current canonical projection) -- same "event stream over snapshots"
principle as EOLMS and the Technology Lifecycle layer. A revision never
edits an entry in place: it creates a NEW entry that supersedes the old
one, and marks the old one `status: superseded` with `superseded_by` set.

Write discipline (feature brief, "Write discipline"): additive by
default. External evidence only supports/challenges/qualifies an entry,
never overwrites it. A derived entry extracted from an imported document
(not built in Phase 1 -- no framework-import pipeline exists yet) would
stay `needs_review: true` until a human confirms it; the user's own
verbatim declarations (authorship="user_authored") are never gated.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import datetime
import os
import re
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "pov"  # .../system/pov

REGISTRY_PATH = ROOT / "registry.json"
EVENTS_PATH = ROOT / "events.jsonl"
EVIDENCE_LINKS_PATH = ROOT / "evidence_links.jsonl"
FRAMEWORKS_DIR = ROOT / "frameworks"

VALID_TYPES = {"principle", "hypothesis", "evaluative_lens", "metric", "research_question", "hard_boundary"}
VALID_STATUSES = {"active", "testing", "qualified", "superseded", "retired"}
VALID_CONVICTIONS = {"working_hypothesis", "informed_belief", "strong_conviction", "foundational_principle"}
VALID_AUTHORSHIPS = {"user_authored", "rbb_inferred"}
VALID_EVIDENCE_RELATIONS = {"supports", "challenges", "qualifies"}


class UserPovError(ValueError):
    """Raised on a structurally invalid entry or evidence link."""


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.date.today().isoformat()


def _assert_in(value, allowed: set, field_name: str) -> None:
    if value not in allowed:
        raise UserPovError(f"{field_name} must be one of {sorted(allowed)}, got {value!r}")


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _save_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _registry_lock_path() -> Path:
    p = ROOT / ".registry.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextlib.contextmanager
def _registry_lock():
    lock_path = _registry_lock_path()
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"entries": []}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _save_registry(reg: dict) -> None:
    _save_json_atomic(REGISTRY_PATH, reg)


def _new_pov_id() -> str:
    return f"pov-{uuid.uuid4().hex[:12]}"


def _empty_entry(
    pov_id: str, statement: str, entry_type: str, scope: str, *, status: str,
    conviction: str, authorship: str, source_document: str | None, source_section: str | None,
    applies_to_surfaces: list[str] | None, needs_review: bool,
) -> dict:
    ts = now_iso()
    return {
        "pov_id": pov_id,
        "statement": statement,
        "type": entry_type,
        "scope": scope,
        "status": status,
        "conviction": conviction,
        "authorship": authorship,
        "needs_review": needs_review,
        "source_document": source_document,
        "source_section": source_section,
        "applies_to_surfaces": applies_to_surfaces or [],
        "supporting_evidence_ids": [],
        "challenging_evidence_ids": [],
        "qualifying_evidence_ids": [],
        "supersedes": None,
        "superseded_by": None,
        "created_at": ts,
        "last_reviewed_at": ts,
    }


def add_pov_entry(
    statement: str, entry_type: str, scope: str, *, status: str = "active",
    conviction: str = "informed_belief", authorship: str = "user_authored",
    source_document: str | None = None, source_section: str | None = None,
    applies_to_surfaces: list[str] | None = None,
) -> dict:
    if not statement or not statement.strip():
        raise UserPovError("statement is required")
    _assert_in(entry_type, VALID_TYPES, "type")
    _assert_in(status, VALID_STATUSES, "status")
    _assert_in(conviction, VALID_CONVICTIONS, "conviction")
    _assert_in(authorship, VALID_AUTHORSHIPS, "authorship")
    # Write discipline: an RBB-inferred entry stays unreviewed until a
    # human confirms it; the user's own verbatim declaration never needs
    # that gate (feature brief, "Write discipline").
    needs_review = authorship == "rbb_inferred"
    pov_id = _new_pov_id()
    entry = _empty_entry(
        pov_id, statement, entry_type, scope, status=status, conviction=conviction,
        authorship=authorship, source_document=source_document, source_section=source_section,
        applies_to_surfaces=applies_to_surfaces, needs_review=needs_review,
    )
    with _registry_lock():
        reg = load_registry()
        reg.setdefault("entries", []).append(entry)
        _save_registry(reg)
    _append_jsonl(EVENTS_PATH, {"event": "created", "pov_id": pov_id, "entry": entry, "recorded_at": now_iso()})
    return entry


def get_pov_entry(pov_id: str) -> dict:
    reg = load_registry()
    for e in reg.get("entries", []):
        if e["pov_id"] == pov_id:
            return e
    raise UserPovError(f"No POV entry with pov_id {pov_id!r}")


def list_pov_entries(*, scope: str | None = None, entry_type: str | None = None, status: str | None = None) -> list[dict]:
    entries = load_registry().get("entries", [])
    if scope is not None:
        entries = [e for e in entries if e.get("scope") == scope]
    if entry_type is not None:
        entries = [e for e in entries if e.get("type") == entry_type]
    if status is not None:
        entries = [e for e in entries if e.get("status") == status]
    return entries


def revise_pov_entry(
    pov_id: str, new_statement: str, *, conviction: str | None = None,
    reason: str | None = None,
) -> dict:
    """Never edits `pov_id` in place -- creates a new entry carrying the
    revised statement, marks the original `status: superseded` with
    `superseded_by` set, and links the new entry's `supersedes` back to
    it. Same append-only/supersedes discipline as the rest of this
    codebase's event-sourced domains."""
    with _registry_lock():
        reg = load_registry()
        entries = reg.setdefault("entries", [])
        original = next((e for e in entries if e["pov_id"] == pov_id), None)
        if original is None:
            raise UserPovError(f"No POV entry with pov_id {pov_id!r}")
        if original["status"] in {"superseded", "retired"}:
            raise UserPovError(f"pov_id {pov_id!r} is already {original['status']} -- revise the entry that supersedes it instead")
        new_pov_id = _new_pov_id()
        new_entry = _empty_entry(
            new_pov_id, new_statement, original["type"], original["scope"],
            status="active", conviction=conviction or original["conviction"],
            authorship=original["authorship"], source_document=original.get("source_document"),
            source_section=original.get("source_section"), applies_to_surfaces=list(original.get("applies_to_surfaces") or []),
            needs_review=False,
        )
        new_entry["supersedes"] = pov_id
        original["status"] = "superseded"
        original["superseded_by"] = new_pov_id
        original["last_reviewed_at"] = now_iso()
        entries.append(new_entry)
        _save_registry(reg)
    _append_jsonl(EVENTS_PATH, {
        "event": "revised", "pov_id": new_pov_id, "supersedes": pov_id, "reason": reason,
        "entry": new_entry, "recorded_at": now_iso(),
    })
    return new_entry


def retire_pov_entry(pov_id: str, *, reason: str) -> dict:
    if not reason or not reason.strip():
        raise UserPovError("reason is required to retire a POV entry")
    with _registry_lock():
        reg = load_registry()
        entry = next((e for e in reg.get("entries", []) if e["pov_id"] == pov_id), None)
        if entry is None:
            raise UserPovError(f"No POV entry with pov_id {pov_id!r}")
        entry["status"] = "retired"
        entry["last_reviewed_at"] = now_iso()
        _save_registry(reg)
    _append_jsonl(EVENTS_PATH, {"event": "retired", "pov_id": pov_id, "reason": reason, "recorded_at": now_iso()})
    return entry


def attach_pov_evidence(
    pov_id: str, relation: str, evidence: str, *, source_url: str | None = None,
    confidence: str = "medium",
) -> dict:
    """Append one evidence record linking to an existing entry --
    supports/challenges/qualifies ONLY (feature brief's "critical
    separation": evidence is independent fact, never itself the belief).
    Never overwrites or removes the entry it's attached to."""
    _assert_in(relation, VALID_EVIDENCE_RELATIONS, "relation")
    entry = get_pov_entry(pov_id)  # raises UserPovError if unknown -- never attach to a nonexistent entry
    evidence_id = f"pov-ev-{uuid.uuid4().hex[:12]}"
    record = {
        "evidence_id": evidence_id, "pov_id": pov_id, "relation": relation, "evidence": evidence,
        "source_url": source_url, "confidence": confidence, "recorded_at": now_iso(),
    }
    _append_jsonl(EVIDENCE_LINKS_PATH, record)
    field = {"supports": "supporting_evidence_ids", "challenges": "challenging_evidence_ids", "qualifies": "qualifying_evidence_ids"}[relation]
    with _registry_lock():
        reg = load_registry()
        for e in reg.get("entries", []):
            if e["pov_id"] == pov_id:
                e.setdefault(field, []).append(evidence_id)
                break
        _save_registry(reg)
    return record


def list_evidence_for(pov_id: str) -> list[dict]:
    return [e for e in _load_jsonl(EVIDENCE_LINKS_PATH) if e.get("pov_id") == pov_id]
