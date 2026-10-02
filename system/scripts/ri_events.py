#!/usr/bin/env python3
"""
ri_events.py — append-only RI event store.

Storage layer for the RI event-sourcing foundation defined in
`system/protocols/P-021_ri_event_sourcing.md` and `system/SCHEMAS.md` →
"Relationship Intelligence Event."

This module is the **storage layer only**. It knows how to:

  * compute dedupe keys from an event payload (per source_type)
  * validate event shape (required fields)
  * generate stable event_ids
  * append a new event to the right monthly JSONL (or recognize it as a
    duplicate and return the existing event_id)
  * load events filtered by date range, source_type, persistence_status, or
    dedupe_key
  * rebuild the secondary index (`system/.cache/ri_events_index.json`)

It does NOT classify, score, or project. That work lives in `ri_intake.py`
and the per-source pre-processors, plus the existing
`manual_relationship_intake.py` engine.

Partitioning anchor: `event_at`. An event for a relationship moment on
2026-04-12 lives in `2026-04.jsonl` even when captured on 2026-05-19. See
the README in this module's sibling directory `system/ri_events/`.

CLI:

    python3 system/scripts/ri_events.py recent [--limit N] [--since ISO]
    python3 system/scripts/ri_events.py get <event_id>
    python3 system/scripts/ri_events.py dedupe <dedupe_key>
    python3 system/scripts/ri_events.py reindex
    python3 system/scripts/ri_events.py --smoke
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------

PROJECT_DIR = core.PROJECT_DIR
SYSTEM_DIR = PROJECT_DIR / "system"
EVENTS_DIR = SYSTEM_DIR / "ri_events"
CACHE_DIR = SYSTEM_DIR / ".cache"
INDEX_PATH = CACHE_DIR / "ri_events_index.json"


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

VALID_SOURCE_TYPES = {
    "manual_text",
    "linkedin_screenshot",
    "fathom_manual_paste",
    "zoom_manual_paste",
    "email_paste",
    "recruiting_update",
    # Future / reserved
    "interaction_brief_backfill",
    "internal_corrective",
    # System-detected passive signals (relationship_signals, linkedin_messaging overlays)
    "passive_signal",
    # Campaign lifecycle facts (invited/registered/declined/attended) emitted by
    # campaign_engine.py — see Conference Campaign Intelligence Engine.
    "campaign_lifecycle",
}

VALID_DEDUPE_DECISIONS = {
    "new_event",
    "duplicate_of_existing",
    "updates_existing_projection",
    "correction_event",
    "same_source_new_signal",
    "stale_signal",
}

VALID_PERSISTENCE_STATUSES = {
    "not_persisted",
    "proposed_write_pending_confirmation",
    "persisted",
    "rejected_by_operator",
    "failed_post_validation",
    "verified",  # legacy label from SCHEMAS.md example; treat as "persisted"
}

VALID_EVENT_AT_CONFIDENCE = {"high", "medium", "low"}

# Required top-level fields on every event. See design doc and SCHEMAS.md.
REQUIRED_TOP_FIELDS = (
    "event_id",
    "captured_at",
    "event_at",
    "event_at_confidence",
    "source",
    "entities",
    "dedupe",
    "signal",
    "persistence",
)

REQUIRED_SOURCE_FIELDS = ("type",)
REQUIRED_DEDUPE_FIELDS = ("dedupe_key", "decision")
REQUIRED_PERSISTENCE_FIELDS = ("status",)
REQUIRED_SIGNAL_FIELDS = ("type",)


# ----------------------------------------------------------------------
# Slug + id helpers
# ----------------------------------------------------------------------

_SLUG_RX = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 40) -> str:
    """Normalize text to a kebab-case slug usable inside event_ids."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text)
    norm = norm.encode("ascii", "ignore").decode("ascii")
    norm = norm.lower()
    norm = _SLUG_RX.sub("-", norm).strip("-")
    if len(norm) > max_len:
        norm = norm[:max_len].rstrip("-")
    return norm


def _hash_text(text: str) -> str:
    """SHA-256 hex digest of a normalized text payload. Prefixed `sha256:`."""
    if text is None:
        return ""
    h = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
    return f"sha256:{h}"


def _event_at_date(event_at: str) -> str:
    """Extract the YYYY-MM-DD portion of an event_at timestamp.

    Tolerates `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SS`, and offset variants.
    """
    if not event_at:
        raise ValueError("event_at is required")
    return event_at[:10]


def _event_at_month(event_at: str) -> str:
    """Return `YYYY-MM` for partition routing. Anchor: event_at."""
    return _event_at_date(event_at)[:7]


def generate_event_id(event_at: str, slug: str, existing_ids: Iterable[str] = ()) -> str:
    """Generate `ri_<YYYY-MM-DD>_<slug>_<seq>` with the lowest available seq.

    `existing_ids` should contain any event_ids already used for the same date
    + slug so the sequence counter monotonically increases. The index passes
    this set in to keep id generation deterministic.
    """
    date_part = _event_at_date(event_at)
    base = f"ri_{date_part}_{slug or 'unspecified'}"
    seq = 1
    taken = {eid for eid in existing_ids if eid.startswith(base + "_")}
    while True:
        candidate = f"{base}_{seq:03d}"
        if candidate not in taken:
            return candidate
        seq += 1


# ----------------------------------------------------------------------
# Dedupe key computation
# ----------------------------------------------------------------------

def _source_stable_id(event: dict) -> str:
    """Compute the source_stable_id portion of the dedupe key.

    Formula table from the design doc:
      fathom_manual_paste : source.id (fathom share URL) or raw_text_hash
      zoom_manual_paste   : raw_text_hash
      email_paste         : message_id or raw_text_hash + subject
      linkedin_screenshot : image SHA-256 (source.raw_text_hash carries it)
      recruiting_update   : recruiter_id + role_slug + event_at_date
      manual_text         : raw_text_hash + entity_signature + event_at_date
      interaction_brief_backfill : brief_id
      internal_corrective : correcting_event_id  (passed in via source.id)
    """
    source = event.get("source") or {}
    stype = source.get("type")
    if not stype:
        raise ValueError("event.source.type is required")

    raw_hash = source.get("raw_text_hash") or ""

    if stype == "fathom_manual_paste":
        return source.get("id") or raw_hash or "no_source_id"

    if stype == "zoom_manual_paste":
        return raw_hash or "no_source_id"

    if stype == "email_paste":
        message_id = source.get("message_id") or source.get("id")
        if message_id:
            return f"msg:{message_id}"
        subject = (source.get("subject") or source.get("title") or "").strip()
        return f"{raw_hash}+{slugify(subject, 50)}" if raw_hash else f"no_msg_id+{slugify(subject, 50)}"

    if stype == "linkedin_screenshot":
        # raw_text_hash carries the image SHA-256 per the pre-processor.
        return raw_hash or source.get("id") or "no_image_id"

    if stype == "recruiting_update":
        entities = event.get("entities") or {}
        recruiter = None
        for p in entities.get("people") or []:
            if p.get("decision") == "matched_existing" and p.get("matched_id"):
                recruiter = p["matched_id"]
                break
        if not recruiter:
            for p in entities.get("people") or []:
                if p.get("raw"):
                    recruiter = slugify(p["raw"])
                    break
        role_slug = source.get("role_slug") or source.get("opportunity") or ""
        if role_slug:
            role_slug = slugify(role_slug, 40)
        return f"{recruiter or 'unknown_recruiter'}+{role_slug}+{_event_at_date(event['event_at'])}"

    if stype == "manual_text":
        entity_sig = _entity_signature(event)
        return f"{raw_hash}+{entity_sig}+{_event_at_date(event['event_at'])}"

    if stype == "interaction_brief_backfill":
        return source.get("id") or source.get("path") or "no_brief_id"

    if stype == "internal_corrective":
        return source.get("id") or "no_correcting_event_id"

    if stype == "passive_signal":
        # Stable id encodes the detector origin so the same signal from the
        # same source does not write a duplicate event on the next run.
        # Formula: <detector_source>:<contact_id_or_slug>:<signal_type>
        # caller must populate source.id with this compound key.
        return source.get("id") or raw_hash or "no_signal_id"

    # Unknown source_type: fall back to the most stable fields we have.
    return raw_hash or source.get("id") or source.get("path") or "unspecified"


def _entity_signature(event: dict) -> str:
    """Stable short hash over the matched/raw people + companies, sorted.

    Used by manual_text dedupe so repeated paste of the same names+topic
    folds together even if whitespace differs in the original text.
    """
    entities = event.get("entities") or {}
    parts: list[str] = []
    for p in entities.get("people") or []:
        parts.append(p.get("matched_id") or slugify(p.get("raw") or "", 30))
    for c in entities.get("companies") or []:
        parts.append("co:" + (c.get("matched_id") or slugify(c.get("raw") or "", 30)))
    parts = sorted(x for x in parts if x)
    joined = "|".join(parts)
    if not joined:
        return "no_entities"
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def compute_dedupe_key(event: dict) -> str:
    """Compute the canonical dedupe key for an event.

    Formula: `<source_type>:<source_stable_id>:<event_at_iso>`.
    """
    source = event.get("source") or {}
    stype = source.get("type")
    if not stype:
        raise ValueError("event.source.type is required to compute a dedupe key")
    event_at = event.get("event_at")
    if not event_at:
        raise ValueError("event.event_at is required to compute a dedupe key")
    sid = _source_stable_id(event)
    return f"{stype}:{sid}:{event_at}"


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

class EventValidationError(ValueError):
    pass


def validate_event(event: dict, *, strict: bool = True) -> list[str]:
    """Return a list of validation errors. Empty list means valid.

    In `strict` mode this raises EventValidationError on the first non-empty
    error list; otherwise it returns the list and lets the caller decide.
    """
    errors: list[str] = []
    if not isinstance(event, dict):
        return ["event must be a dict"]

    for field in REQUIRED_TOP_FIELDS:
        if field not in event:
            errors.append(f"missing required field: {field}")

    src = event.get("source")
    if not isinstance(src, dict):
        errors.append("source must be a dict")
    else:
        for field in REQUIRED_SOURCE_FIELDS:
            if field not in src:
                errors.append(f"source.{field} is required")
        if src.get("type") not in VALID_SOURCE_TYPES:
            errors.append(
                f"source.type={src.get('type')!r} is not in VALID_SOURCE_TYPES"
            )

    dedupe = event.get("dedupe")
    if not isinstance(dedupe, dict):
        errors.append("dedupe must be a dict")
    else:
        for field in REQUIRED_DEDUPE_FIELDS:
            if field not in dedupe:
                errors.append(f"dedupe.{field} is required")
        if dedupe.get("decision") not in VALID_DEDUPE_DECISIONS:
            errors.append(
                f"dedupe.decision={dedupe.get('decision')!r} is not in VALID_DEDUPE_DECISIONS"
            )

    persistence = event.get("persistence")
    if not isinstance(persistence, dict):
        errors.append("persistence must be a dict")
    else:
        for field in REQUIRED_PERSISTENCE_FIELDS:
            if field not in persistence:
                errors.append(f"persistence.{field} is required")
        if persistence.get("status") not in VALID_PERSISTENCE_STATUSES:
            errors.append(
                f"persistence.status={persistence.get('status')!r} is not in VALID_PERSISTENCE_STATUSES"
            )

    signal = event.get("signal")
    if not isinstance(signal, dict):
        errors.append("signal must be a dict")
    else:
        for field in REQUIRED_SIGNAL_FIELDS:
            if field not in signal:
                errors.append(f"signal.{field} is required")

    confidence = event.get("event_at_confidence")
    if confidence and confidence not in VALID_EVENT_AT_CONFIDENCE:
        errors.append(
            f"event_at_confidence={confidence!r} is not in VALID_EVENT_AT_CONFIDENCE"
        )
    if confidence and confidence != "high" and not event.get("event_at_source"):
        errors.append(
            "event_at_source is required when event_at_confidence != 'high'"
        )

    entities = event.get("entities")
    if isinstance(entities, dict):
        for p in entities.get("people") or []:
            if "decision" not in p:
                errors.append("entities.people[].decision is required on every person")
                break
        for c in entities.get("companies") or []:
            if "decision" not in c:
                errors.append("entities.companies[].decision is required on every company")
                break

    if strict and errors:
        raise EventValidationError("; ".join(errors))
    return errors


# ----------------------------------------------------------------------
# JSONL I/O
# ----------------------------------------------------------------------

def _ensure_dirs() -> None:
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _partition_path(event_at: str) -> Path:
    month = _event_at_month(event_at)
    return EVENTS_DIR / f"{month}.jsonl"


def _to_storage_path(path: Path) -> str:
    """Render a partition path for persistence in the index.

    Normally returns a path relative to PROJECT_DIR (so the repo can move
    around without invalidating the index). Falls back to an absolute path
    when `path` lives outside PROJECT_DIR — that case only fires in the
    smoke test, which rebinds EVENTS_DIR to a tmpdir."""
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path.resolve())


def _from_storage_path(file_rel: str) -> Path:
    """Inverse of `_to_storage_path`. Absolute paths pass through unchanged."""
    p = Path(file_rel)
    if p.is_absolute():
        return p
    return PROJECT_DIR / p


def _atomic_append(path: Path, line: str) -> None:
    """Append a single JSONL line atomically.

    A simple `open(..., 'a')` is atomic on POSIX for a single write smaller
    than PIPE_BUF (4 KB on Linux/Mac). RI event lines are well under that.
    We still do an explicit os.fsync to be safe across crashes — these are
    canonical records.
    """
    _ensure_dirs()
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")
        f.flush()
        os.fsync(f.fileno())


def _iter_jsonl(path: Path) -> Iterable[tuple[int, dict]]:
    """Yield (line_index, event_dict) pairs from a JSONL file. Skips blanks."""
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError as e:
                # A canonical store should never have corrupt lines; surface
                # this loudly rather than silently dropping.
                raise RuntimeError(
                    f"corrupt JSONL line in {path} at index {i}: {e}"
                ) from e


def _all_jsonl_files() -> list[Path]:
    if not EVENTS_DIR.exists():
        return []
    return sorted(p for p in EVENTS_DIR.glob("*.jsonl"))


# ----------------------------------------------------------------------
# Index
# ----------------------------------------------------------------------

def _file_fingerprint(path: Path) -> dict:
    st = path.stat()
    return {"path": _to_storage_path(path), "mtime": int(st.st_mtime), "size": st.st_size}


def _empty_index() -> dict:
    return {
        "header": {
            "rebuilt_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": [],
        },
        "by_event_id": {},
        "by_dedupe_key": {},
    }


def load_index() -> dict:
    if not INDEX_PATH.exists():
        return _empty_index()
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Corrupt index → treat as missing; caller can reindex.
        return _empty_index()


def _save_index(index: dict) -> None:
    _ensure_dirs()
    INDEX_PATH.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def _index_event(index: dict, event: dict, *, file_rel: str, line_idx: int, count: int) -> None:
    entities = event.get("entities") or {}
    people_summary = [
        p.get("matched_id") or p.get("raw") or "?"
        for p in (entities.get("people") or [])
    ]
    companies_summary = [
        c.get("matched_id") or c.get("raw") or "?"
        for c in (entities.get("companies") or [])
    ]
    index["by_event_id"][event["event_id"]] = {
        "file": file_rel,
        "line": line_idx,
        "event_at": event.get("event_at"),
        "captured_at": event.get("captured_at"),
        "source_type": (event.get("source") or {}).get("type"),
        "dedupe_key": (event.get("dedupe") or {}).get("dedupe_key"),
        "dedupe_decision": (event.get("dedupe") or {}).get("decision"),
        "persistence_status": (event.get("persistence") or {}).get("status"),
        "signal_type": (event.get("signal") or {}).get("type"),
        "people": people_summary[:8],
        "companies": companies_summary[:8],
    }
    key = (event.get("dedupe") or {}).get("dedupe_key")
    if key:
        index["by_dedupe_key"].setdefault(key, []).append(event["event_id"])


def reindex() -> dict:
    """Rebuild the index from scratch by walking every JSONL file."""
    index = _empty_index()
    files = _all_jsonl_files()
    file_counts: dict[str, int] = {}
    for path in files:
        rel = _to_storage_path(path)
        file_counts[rel] = 0
        for line_idx, event in _iter_jsonl(path):
            file_counts[rel] += 1
            try:
                validate_event(event, strict=False)
            except Exception:
                pass  # validation errors recorded but don't halt the reindex
            _index_event(index, event, file_rel=rel, line_idx=line_idx, count=file_counts[rel])

    sources_header: list[dict] = []
    for path in files:
        fp = _file_fingerprint(path)
        fp["count"] = file_counts.get(fp["path"], 0)
        sources_header.append(fp)
    index["header"]["sources"] = sources_header
    index["header"]["rebuilt_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_index(index)
    return index


# ----------------------------------------------------------------------
# Public append + read API
# ----------------------------------------------------------------------

class AppendResult(dict):
    """Dict subclass so call sites can pattern-match on the keys easily.

    Always carries: `event_id`, `was_appended` (bool), `decision`
    (`new_event`, `duplicate_of_existing`, etc.), `file` (relative path),
    `existing_event_id` (set when was_appended is False).
    """


def append(event: dict, *, allow_existing_id: bool = False) -> AppendResult:
    """Append `event` to the appropriate JSONL partition, with dedupe.

    Behavior:
      1. Validate the event shape (strict — raises on missing required fields).
      2. Compute dedupe.dedupe_key if missing; otherwise validate that it
         matches the recomputed key (to catch hand-edited drift).
      3. If the dedupe_key already exists in the index, return the existing
         event_id WITHOUT writing. The result records `was_appended=False`
         and `decision="duplicate_of_existing"`.
      4. Otherwise, write to `system/ri_events/<YYYY-MM>.jsonl` (partition
         keyed on event_at) and update the index in memory + on disk.

    Note on event_id collisions: if the caller passed an event_id already in
    the index but the dedupe_key is different (e.g., manual reuse), this is
    a programmer error and raises unless allow_existing_id=True.
    """
    if not isinstance(event, dict):
        raise EventValidationError("event must be a dict")

    # Auto-populate fields the caller often omits.
    event.setdefault("captured_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    dedupe_block = event.setdefault("dedupe", {})
    computed_key = compute_dedupe_key(event)
    if "dedupe_key" in dedupe_block:
        if dedupe_block["dedupe_key"] != computed_key:
            raise EventValidationError(
                f"dedupe.dedupe_key={dedupe_block['dedupe_key']!r} does not "
                f"match the computed key {computed_key!r}"
            )
    else:
        dedupe_block["dedupe_key"] = computed_key
    dedupe_block.setdefault("decision", "new_event")
    dedupe_block.setdefault("duplicates", [])

    # Allow event_id to be generated from a slug if absent.
    if "event_id" not in event:
        slug = event.pop("_slug", None) or _default_slug_from(event)
        index = load_index()
        event["event_id"] = generate_event_id(event["event_at"], slug, index["by_event_id"].keys())

    validate_event(event, strict=True)

    index = load_index()

    # Dedupe check.
    existing_for_key = index["by_dedupe_key"].get(dedupe_block["dedupe_key"]) or []
    if existing_for_key:
        return AppendResult(
            event_id=existing_for_key[0],
            existing_event_id=existing_for_key[0],
            was_appended=False,
            decision="duplicate_of_existing",
            file=index["by_event_id"].get(existing_for_key[0], {}).get("file"),
        )

    # Event-id collision (different dedupe_key).
    if event["event_id"] in index["by_event_id"] and not allow_existing_id:
        raise EventValidationError(
            f"event_id={event['event_id']!r} already exists in the index with a "
            f"different dedupe_key; refusing to silently overwrite."
        )

    # Persist.
    partition = _partition_path(event["event_at"])
    line = json.dumps(event, separators=(",", ":"), sort_keys=True)
    # Line number is the count of existing non-empty lines in this partition.
    existing_lines = sum(1 for _ in _iter_jsonl(partition)) if partition.exists() else 0
    _atomic_append(partition, line)
    file_rel = _to_storage_path(partition)
    _index_event(index, event, file_rel=file_rel, line_idx=existing_lines, count=existing_lines + 1)
    # Refresh the source header for this partition.
    fp = _file_fingerprint(partition)
    new_sources = [s for s in index["header"]["sources"] if s["path"] != file_rel]
    fp["count"] = existing_lines + 1
    new_sources.append(fp)
    index["header"]["sources"] = sorted(new_sources, key=lambda x: x["path"])
    index["header"]["rebuilt_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_index(index)

    return AppendResult(
        event_id=event["event_id"],
        was_appended=True,
        decision=dedupe_block["decision"],
        file=file_rel,
    )


def _default_slug_from(event: dict) -> str:
    """Best-effort slug if the caller didn't provide one."""
    entities = event.get("entities") or {}
    for p in entities.get("people") or []:
        candidate = p.get("matched_id") or p.get("raw")
        if candidate:
            return slugify(candidate, 30)
    for c in entities.get("companies") or []:
        candidate = c.get("matched_id") or c.get("raw")
        if candidate:
            return slugify(candidate, 30)
    return (event.get("signal") or {}).get("type") or "event"


def find_by_event_id(event_id: str) -> dict | None:
    index = load_index()
    entry = index["by_event_id"].get(event_id)
    if not entry:
        return None
    return _load_event_from_partition(entry["file"], entry["line"], event_id)


def find_by_dedupe_key(dedupe_key: str) -> list[dict]:
    index = load_index()
    ids = index["by_dedupe_key"].get(dedupe_key) or []
    out: list[dict] = []
    for eid in ids:
        ev = find_by_event_id(eid)
        if ev:
            out.append(ev)
    return out


def _load_event_from_partition(file_rel: str, line_idx: int, expected_id: str | None = None) -> dict | None:
    path = _from_storage_path(file_rel)
    for i, ev in _iter_jsonl(path):
        if i == line_idx:
            if expected_id and ev.get("event_id") != expected_id:
                # Index is stale; fall back to scan for the id below.
                break
            return ev
    if expected_id:
        for _, ev in _iter_jsonl(path):
            if ev.get("event_id") == expected_id:
                return ev
    return None


def _event_matches_entity(event: dict, entity_id: str) -> bool:
    entities = event.get("entities") or {}
    for p in entities.get("people") or []:
        if p.get("matched_id") == entity_id or p.get("id") == entity_id:
            return True
    for c in entities.get("companies") or []:
        if c.get("matched_id") == entity_id or c.get("id") == entity_id:
            return True
    return False


def load_events(
    *,
    since: str | None = None,
    until: str | None = None,
    source_type: str | None = None,
    persistence_status: str | None = None,
    entity_id: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Read events from the JSONL stream, filtered.

    `since` and `until` filter on `captured_at` (ISO string compare is fine
    for ISO 8601 with consistent offsets). Source/persistence filters are
    exact-match strings. `entity_id` matches against `entities.people[]`/
    `entities.companies[]` by `matched_id` (falling back to `id`) — answers
    "which events touch this person/company" without the caller having to
    scan the JSONL by hand. `limit` caps the result count, newest-first.
    """
    out: list[dict] = []
    files = _all_jsonl_files()
    # Walk newest partition first so `limit` returns the freshest events.
    for path in reversed(files):
        for _, ev in _iter_jsonl(path):
            captured = ev.get("captured_at") or ""
            if since and captured < since:
                continue
            if until and captured > until:
                continue
            if source_type and (ev.get("source") or {}).get("type") != source_type:
                continue
            if persistence_status and (ev.get("persistence") or {}).get("status") != persistence_status:
                continue
            if entity_id and not _event_matches_entity(ev, entity_id):
                continue
            out.append(ev)
            if limit and len(out) >= limit:
                return out
    # newest-first ordering across partitions
    out.sort(key=lambda e: e.get("captured_at") or "", reverse=True)
    if limit:
        out = out[:limit]
    return out


# ----------------------------------------------------------------------
# Smoke test
# ----------------------------------------------------------------------

def _smoke() -> int:
    """End-to-end write→read→dedupe→reindex cycle.

    Writes test events to an isolated `_smoke/` partition under EVENTS_DIR
    so the real stream stays untouched, then cleans up. Returns 0 on success,
    1 on failure.
    """
    import tempfile
    import shutil
    global EVENTS_DIR, INDEX_PATH  # noqa: PLW0603 — intentional rebinding for the smoke run
    orig_events_dir = EVENTS_DIR
    orig_index_path = INDEX_PATH
    tmp = Path(tempfile.mkdtemp(prefix="ri_events_smoke_"))
    EVENTS_DIR = tmp / "ri_events"
    INDEX_PATH = tmp / "ri_events_index.json"
    failures = 0

    def expect(label: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        if cond:
            print(f"  OK   {label}")
        else:
            failures += 1
            print(f"  FAIL {label}  {detail}")

    try:
        # Event 1 — Fathom transcript, PerfectHire fixture, event_at on 2026-05-19.
        ev1 = {
            "event_at": "2026-05-19T10:00:00-05:00",
            "event_at_confidence": "high",
            "source": {
                "type": "fathom_manual_paste",
                "id": "fathom://share/test-001",
                "title": "Todd <> PerfectHire - QSR Platform Review - May 19",
                "raw_text_hash": _hash_text("the perfecthire test transcript"),
            },
            "entities": {
                "people": [
                    {"raw": "Olivia Nielsen", "matched_id": "olivia-nielsen", "decision": "matched_existing"},
                    {"raw": "Max Holmes", "matched_id": None, "decision": "propose_new_contact"},
                ],
                "companies": [
                    {"raw": "PerfectHire", "matched_id": None, "decision": "propose_new_company"},
                ],
            },
            "dedupe": {"decision": "new_event"},
            "signal": {"type": "advisory_opportunity"},
            "persistence": {"status": "proposed_write_pending_confirmation"},
            "trace_id": "T-2026-05-19-005",
        }
        r1 = append(ev1)
        expect("append ev1 was_appended=True", r1["was_appended"] is True, f"got {r1}")
        expect("append ev1 file ends with 2026-05.jsonl", r1["file"].endswith("2026-05.jsonl"), f"file={r1['file']}")
        expect("event_id generated", r1["event_id"].startswith("ri_2026-05-19_"), f"event_id={r1['event_id']}")

        # Event 2 — same payload, should dedupe.
        ev1b = json.loads(json.dumps(ev1))  # deep copy
        ev1b.pop("event_id", None)
        ev1b.get("dedupe", {}).pop("dedupe_key", None)
        r2 = append(ev1b)
        expect("re-append same payload deduped", r2["was_appended"] is False, f"got {r2}")
        expect("re-append returns the same event_id", r2["event_id"] == r1["event_id"])
        expect("re-append decision=duplicate_of_existing", r2["decision"] == "duplicate_of_existing")

        # Event 3 — different fathom share, same date → new event.
        ev2 = json.loads(json.dumps(ev1))
        ev2.pop("event_id", None)
        ev2.get("dedupe", {}).pop("dedupe_key", None)
        ev2["source"]["id"] = "fathom://share/test-002"
        ev2["source"]["raw_text_hash"] = _hash_text("a different transcript")
        r3 = append(ev2)
        expect("different fathom share appended", r3["was_appended"] is True)
        expect("seq counter advanced", r3["event_id"] != r1["event_id"])

        # Event 4 — event_at in April → partition 2026-04.jsonl.
        ev3 = json.loads(json.dumps(ev1))
        ev3.pop("event_id", None)
        ev3.get("dedupe", {}).pop("dedupe_key", None)
        ev3["event_at"] = "2026-04-12T15:30:00-05:00"
        ev3["source"]["id"] = "fathom://share/test-003-april"
        ev3["source"]["raw_text_hash"] = _hash_text("an april meeting")
        r4 = append(ev3)
        expect("april event lands in 2026-04 partition", r4["file"].endswith("2026-04.jsonl"), f"file={r4['file']}")

        # Event 5 — recruiting_update dedupe key uses recruiter+role+date.
        ev4 = {
            "event_at": "2026-05-15T14:00:00-05:00",
            "event_at_confidence": "medium",
            "event_at_source": "operator_phrase_friday",
            "source": {
                "type": "recruiting_update",
                "role_slug": "hari-mcdonalds-account",
            },
            "entities": {
                "people": [
                    {"raw": "Simin Gorgulu", "matched_id": "simin-gorgulu", "decision": "matched_existing"},
                ],
                "companies": [{"raw": "TritonExec", "decision": "propose_new_company"}],
            },
            "dedupe": {"decision": "new_event"},
            "signal": {"type": "recruiter_screen_completed"},
            "persistence": {"status": "proposed_write_pending_confirmation"},
            "trace_id": "T-2026-05-19-006",
        }
        r5 = append(ev4)
        expect("recruiting_update appended", r5["was_appended"] is True)
        # Re-append: should dedupe by recruiter+role+date.
        ev4b = json.loads(json.dumps(ev4))
        ev4b.pop("event_id", None)
        ev4b.get("dedupe", {}).pop("dedupe_key", None)
        r5b = append(ev4b)
        expect("recruiting_update re-appended is dedupe hit", r5b["was_appended"] is False)

        # Reindex from scratch and check counts match.
        # Four distinct events were appended (ev1 fathom-May, ev2 fathom-May,
        # ev3 fathom-Apr, ev4 recruiting-May). Two re-appends deduped.
        rebuilt = reindex()
        expect(
            "reindex covers all written events",
            len(rebuilt["by_event_id"]) == 4,
            f"expected 4 distinct events, got {len(rebuilt['by_event_id'])}",
        )

        # Filtered load — three of the four are fathom_manual_paste.
        recent = load_events(source_type="fathom_manual_paste")
        expect("load_events filters by source_type", len(recent) == 3, f"got {len(recent)}")

        # find_by_event_id round trip.
        found = find_by_event_id(r1["event_id"])
        expect("find_by_event_id round trip", found is not None and found["event_id"] == r1["event_id"])

        # find_by_dedupe_key round trip.
        idx = load_index()
        first_key = next(iter(idx["by_dedupe_key"].keys()))
        found_by_key = find_by_dedupe_key(first_key)
        expect("find_by_dedupe_key returns >=1", len(found_by_key) >= 1)

        # Validation rejects malformed event.
        try:
            append({"source": {"type": "manual_text"}, "event_at": "2026-05-19", "entities": {}, "signal": {}})
            expect("malformed event raises", False, "did not raise")
        except EventValidationError:
            expect("malformed event raises", True)

    finally:
        EVENTS_DIR = orig_events_dir
        INDEX_PATH = orig_index_path
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"--- smoke complete: {failures} failure(s) ---")
    return 1 if failures else 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RI event store CLI.")
    p.add_argument("--smoke", action="store_true", help="Run the smoke test in an isolated tmpdir.")
    sub = p.add_subparsers(dest="cmd")

    p_recent = sub.add_parser("recent", help="Show recent events.")
    p_recent.add_argument("--limit", type=int, default=20)
    p_recent.add_argument("--since", help="ISO timestamp (captured_at filter)")
    p_recent.add_argument("--source-type")
    p_recent.add_argument("--persistence-status")

    p_get = sub.add_parser("get", help="Lookup one event by id.")
    p_get.add_argument("event_id")

    p_dedupe = sub.add_parser("dedupe", help="Lookup events by dedupe_key.")
    p_dedupe.add_argument("dedupe_key")

    sub.add_parser("reindex", help="Rebuild the index from JSONL.")

    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()

    if args.cmd == "recent":
        events = load_events(
            since=args.since,
            source_type=args.source_type,
            persistence_status=args.persistence_status,
            limit=args.limit,
        )
        for ev in events:
            print(json.dumps({
                "event_id": ev.get("event_id"),
                "event_at": ev.get("event_at"),
                "captured_at": ev.get("captured_at"),
                "source_type": (ev.get("source") or {}).get("type"),
                "signal_type": (ev.get("signal") or {}).get("type"),
                "persistence_status": (ev.get("persistence") or {}).get("status"),
                "people": [p.get("matched_id") or p.get("raw") for p in (ev.get("entities") or {}).get("people") or []],
            }))
        return 0

    if args.cmd == "get":
        ev = find_by_event_id(args.event_id)
        if not ev:
            print(f"not found: {args.event_id}", file=sys.stderr)
            return 1
        print(json.dumps(ev, indent=2))
        return 0

    if args.cmd == "dedupe":
        events = find_by_dedupe_key(args.dedupe_key)
        if not events:
            print(f"no events for dedupe_key: {args.dedupe_key}", file=sys.stderr)
            return 1
        print(json.dumps(events, indent=2))
        return 0

    if args.cmd == "reindex":
        idx = reindex()
        print(json.dumps({
            "events": len(idx["by_event_id"]),
            "files": len(idx["header"]["sources"]),
            "rebuilt_at": idx["header"]["rebuilt_at"],
        }, indent=2))
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
