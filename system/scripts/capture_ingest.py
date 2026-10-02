#!/usr/bin/env python3
"""
capture_ingest.py — RB Intelligence Capture System (ICS) source sweeper.

Scans all enabled capture sources defined in settings.json for new transcript
files, extracts text (transcribing audio if needed), and queues each capture
as a pending JSON file in system/captures/pending/.

Intelligence extraction is NOT done here — see process_pending_captures.py,
which morning_pipeline.py runs immediately after this script's scan step so
every capture queued here gets triaged automatically the same run (RB-DEFECT-
2026-07-09: extraction used to require a live Custom GPT chat command and
captures could sit pending across multiple brief cycles if that was never
said). The chat command still works for anything recorded mid-day:
  - On-demand: "RB, process my Jeff Wayman meeting" → GPT finds and submits

This script is called by morning_pipeline.py as the capture_ingest_scan step.

Usage:
    python3 system/scripts/capture_ingest.py --scan          # sweep all enabled sources
    python3 system/scripts/capture_ingest.py --scan --source just_press_record
    python3 system/scripts/capture_ingest.py --pending       # list queued captures
    python3 system/scripts/capture_ingest.py --queue FILE    # manually queue a file
    python3 system/scripts/capture_ingest.py --smoke
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import audit_log  # noqa: E402

SYSTEM_DIR = core.SYSTEM_DIR
CAPTURES_DIR = SYSTEM_DIR / "captures"
PENDING_DIR = CAPTURES_DIR / "pending"
PROCESSED_DIR = CAPTURES_DIR / "processed"
REGISTRY_PATH = CAPTURES_DIR / ".registry.json"
SETTINGS_PATH = core.SETTINGS_PATH


# ---------------------------------------------------------------------------
# Settings loader
# ---------------------------------------------------------------------------

def _load_sources(source_filter: str | None = None) -> list[dict]:
    """Return enabled folder-watch capture sources from settings.json.

    Sources with connector=="api" (e.g. Granola) are handled by their own
    adapter scripts, not this folder sweep — they're excluded here since they
    have no `folder` key and would otherwise resolve to the working directory.
    """
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        sources = settings.get("capture_sources", {}).get("sources", [])
    except (OSError, json.JSONDecodeError):
        sources = []

    enabled = [s for s in sources if s.get("enabled") and s.get("connector", "folder") == "folder"]
    if source_filter:
        enabled = [s for s in enabled if s.get("id") == source_filter]
    return enabled


def _sweep_window_hours() -> int:
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return int(settings.get("capture_sources", {}).get("processing", {}).get("sweep_window_hours", 24))
    except Exception:
        return 24


# ---------------------------------------------------------------------------
# Registry — deduplication across all sources
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"processed_ids": {}, "last_scan": None}


def _save_registry(reg: dict) -> None:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _file_id(path: Path) -> str:
    """Stable dedup ID from path alone.

    Source filenames are timestamp-unique per recording, so path is a safe
    dedup key on its own. Previously this hashed path+size, which broke for
    iCloud-synced files: a still-undownloaded placeholder reports a smaller
    size than the fully materialized file, so the same unprocessed recording
    got a new file_id (and a duplicate pending capture) on every scan until it
    finished syncing.
    """
    key = str(path)
    return "cap-" + hashlib.sha256(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# File discovery per source
# ---------------------------------------------------------------------------

def _expand_folder(folder: str) -> Path:
    path = Path(folder).expanduser()
    if not path.is_absolute():
        path = core.SYSTEM_DIR.parent / path
    return path.resolve()


def _find_new_files(source: dict, registry: dict, since: datetime) -> list[Path]:
    """Return files matching source patterns that are not yet in the
    registry.

    RB-2026-08-28: `since` used to be a hard discovery gate (skip anything
    older than `sweep_window_hours`, default 24h) on top of the dedup
    registry check. That's redundant with the registry in the normal case
    (an already-processed file is already excluded via `fid in processed`)
    and actively destructive whenever a sweep is missed for longer than the
    window -- confirmed live: 5 real JPR recordings across 3 dates
    (2026-07-28 x2, 2026-08-13 x2, 2026-08-14 x1), one of them 32MB/a real
    substantive meeting, sat fully synced in the source folder and were
    NEVER queued, because by the time the next sweep ran, their mtime had
    already aged past the 24h window -- there is no other path back to
    them, since the registry only tracks what WAS queued, not what should
    have been. The registry-based dedup check below is sufficient on its
    own and doesn't have this failure mode: a file either gets picked up on
    the first sweep after it appears, or (if a sweep was missed) on the
    next one that runs, no matter how much time passed. `since` is kept as
    a parameter (used for the `sweep()` result's `since` field only) rather
    than removed, to avoid a wider signature change."""
    folder = _expand_folder(source.get("folder", ""))
    if not folder.exists():
        return []

    processed = set(registry.get("processed_ids", {}).keys())
    patterns = source.get("patterns", ["**/*.txt"])

    # Collect all matching files
    candidates: list[tuple[float, Path]] = []
    for pattern in patterns:
        for path in folder.glob(pattern):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
                size = path.stat().st_size
            except OSError:
                continue
            if size < 10:
                continue
            fid = _file_id(path)
            if fid in processed:
                continue
            candidates.append((mtime, path))

    # For sources that produce .txt + .m4a pairs, prefer .txt
    # Deduplicate by stem: if a .txt exists for a stem, drop the .m4a
    by_stem: dict[str, Path] = {}
    for _, path in sorted(candidates, reverse=True):
        stem = path.stem
        existing = by_stem.get(stem)
        if existing is None:
            by_stem[stem] = path
        elif existing.suffix.lower() == ".m4a" and path.suffix.lower() == ".txt":
            by_stem[stem] = path  # prefer .txt over .m4a

    return sorted(by_stem.values(), key=lambda p: p.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def _read_vtt(path: Path) -> str:
    """Strip VTT timing lines, return plain transcript text."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line or line == "WEBVTT" or re.match(r"^\d+$", line):
            continue
        if re.match(r"[\d:.,\s]+-->", line):
            continue
        text_lines.append(line)
    return " ".join(text_lines)


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
# Mirrors rbb_chat.py's CHEAP_MODEL -- deliberately not imported from there
# (that module builds a whole FastAPI app + live OpenAI client at import
# time, wrong to pull into a standalone batch script). Same tier reasoning
# either way: RBB_MODEL_TIER_POLICY_2026-09-19.md's Tier 1 ("Extract text
# from an uploaded PDF/Excel/image").
_OCR_MODEL = "gpt-4o-mini"


def _ocr_image(image_path: Path) -> str:
    """OCR a screenshot/image dropped in a watched folder via a cheap
    vision-model call -- same model and prompt rbb_chat.py's own live
    /upload path already uses (_extract_text_from_image), just run here at
    batch-sweep time instead of paid for immediately on a live chat turn.
    This is the concrete point of RB-2026-09-20's "drop a screenshot into a
    synced Drive/iCloud folder instead of the live API" design: the file
    reaches this exact function through the ordinary watched-folder sweep,
    with zero live model cost until the scheduled batch runs.

    Raises on infra failure (missing package/key, API error), like
    _transcribe_whisper_api above -- unlike rbb_chat.py's best-effort
    return-None convention (right for a live endpoint that must not block
    on this), a failure here should surface as a real sweep() error
    (caught by its own per-file try/except), not a queued capture with an
    unexplained empty transcript.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed — run: pip install openai")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    print(f"[capture_ingest] OCR'ing {image_path.name} via {_OCR_MODEL}...", file=sys.stderr)
    content_type = _IMAGE_CONTENT_TYPES.get(image_path.suffix.lower(), "image/png")
    b64_content = base64.b64encode(image_path.read_bytes()).decode("ascii")
    resp = client.responses.create(
        model=_OCR_MODEL,
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "Transcribe all visible text in this image verbatim, in reading order "
                        "(this may be a screenshot of a social post, email, message, or document). "
                        "Then, in one sentence, describe what kind of content it is. Only describe "
                        "what is actually visible -- do not guess at anything not shown."
                    ),
                },
                {"type": "input_image", "image_url": f"data:{content_type};base64,{b64_content}", "detail": "high"},
            ],
        }],
    )
    return (getattr(resp, "output_text", None) or "").strip()


_DOCUMENT_EXTENSIONS = (".xlsx", ".xlsm", ".docx", ".pdf")
# transcription_method values that mean "a real document was mechanically
# extracted, not a conversation" -- a capture with one of these methods
# should default to "pasted_content", never _detect_capture_type()'s
# meeting-oriented keyword fallback (see sweep()/queue_file() below).
_NON_MEETING_METHODS = {"image_ocr", "document_extraction"}


def _extract_text_from_xlsx(content: bytes) -> str:
    """Flatten an .xlsx/.xlsm workbook into plain text. Mirrors server.py's
    _extract_text_from_xlsx's parsing logic (openpyxl, mechanical row-by-row,
    never LLM-guessed) -- duplicated rather than imported, same reasoning as
    _ocr_image above (server.py builds a whole live FastAPI app at import
    time), AND deliberately different failure semantics: server.py's version
    swallows every failure into "" for a live endpoint's own diagnostic
    fallback; this one raises, like _transcribe_whisper_api, so a batch-sweep
    infra problem (missing openpyxl, a genuinely corrupt file) surfaces as a
    real sweep() error instead of a silently empty capture.
    """
    import io as _io
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl not installed — run: pip install openpyxl")
    wb = openpyxl.load_workbook(_io.BytesIO(content), data_only=True, read_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        sheet_lines: list[str] = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip() != ""]
            if cells:
                sheet_lines.append(" | ".join(cells))
        if sheet_lines:
            lines.append(f"## Sheet: {ws.title}")
            lines.extend(sheet_lines)
    return "\n".join(lines).strip()


def _extract_text_from_docx(content: bytes) -> str:
    """Flatten a .docx document (paragraphs + table cells) into plain text.
    Mirrors server.py's _extract_text_from_docx (python-docx, mechanical,
    never LLM-guessed); duplicated with raise-on-failure semantics for the
    same reason as _extract_text_from_xlsx above."""
    import io as _io
    try:
        import docx
    except ImportError:
        raise RuntimeError("python-docx not installed — run: pip install python-docx")
    doc = docx.Document(_io.BytesIO(content))
    lines: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            lines.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines).strip()


def _extract_text_from_pdf(content: bytes) -> str:
    """Flatten a .pdf's page text into plain text via pypdf -- the one
    structural signal a PDF reliably carries without OCR/layout inference.
    Mirrors server.py's _extract_text_from_pdf; duplicated with
    raise-on-failure semantics for the same reason as
    _extract_text_from_xlsx above. Returns "" (not a raise) for a
    scanned/image-only PDF with no extractable text layer -- that is a real,
    expected outcome for some PDFs, not an infra failure."""
    import io as _io
    try:
        import pypdf
    except ImportError:
        raise RuntimeError("pypdf not installed — run: pip install pypdf")
    reader = pypdf.PdfReader(_io.BytesIO(content))
    lines: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def _extract_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    content = path.read_bytes()
    if suffix in (".xlsx", ".xlsm"):
        return _extract_text_from_xlsx(content)
    if suffix == ".docx":
        return _extract_text_from_docx(content)
    return _extract_text_from_pdf(content)


def _extract_text(path: Path, transcription_mode: str) -> tuple[str, str]:
    """Return (transcript_text, transcription_method).

    transcription_method: pre_transcribed | whisper_local | whisper_api | image_ocr | document_extraction | unavailable
    """
    suffix = path.suffix.lower()

    # Plain text / VTT — read directly regardless of mode setting
    if suffix in (".txt", ".md", ".markdown"):
        return path.read_text(encoding="utf-8", errors="replace").strip(), "pre_transcribed"
    if suffix in (".vtt",):
        return _read_vtt(path), "pre_transcribed"
    # Image — OCR directly regardless of mode setting, same reasoning as
    # text/VTT above: there is exactly one sane way to turn a screenshot
    # into text (a cheap vision-model call), not a per-source transcription
    # choice the way local-vs-API Whisper is for audio below.
    if suffix in _IMAGE_EXTENSIONS:
        return _ocr_image(path), "image_ocr"
    # PDF/Excel/Word — mechanical extraction, same "regardless of mode"
    # reasoning as text/VTT/image above.
    if suffix in _DOCUMENT_EXTENSIONS:
        return _extract_document_text(path), "document_extraction"

    # Audio file — need transcription
    if suffix not in (".m4a", ".mp3", ".mp4", ".wav", ".ogg"):
        return "", "unavailable"

    if transcription_mode == "whisper_local":
        return _transcribe_whisper_local(path), "whisper_local"
    if transcription_mode == "whisper_api":
        return _transcribe_whisper_api(path), "whisper_api"

    # No transcription configured — queue with empty transcript, note it
    return "", "unavailable"


def _transcribe_whisper_local(audio_path: Path) -> str:
    try:
        import whisper
    except ImportError:
        raise RuntimeError("openai-whisper not installed — run: pip install openai-whisper")
    # Ensure ffmpeg is on PATH — use static binary if system ffmpeg not available
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
    except ImportError:
        pass
    print(f"[capture_ingest] Transcribing {audio_path.name} via local Whisper...", file=sys.stderr)
    model = whisper.load_model("base")
    result = model.transcribe(str(audio_path))
    return result.get("text", "").strip()


def _transcribe_whisper_api(audio_path: Path) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed — run: pip install openai")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    print(f"[capture_ingest] Transcribing {audio_path.name} via Whisper API...", file=sys.stderr)
    with audio_path.open("rb") as f:
        result = client.audio.transcriptions.create(model="whisper-1", file=f, response_format="text")
    return result.strip() if isinstance(result, str) else result.text.strip()


# ---------------------------------------------------------------------------
# Capture type detection
# ---------------------------------------------------------------------------

def _detect_capture_type(text: str, source: dict) -> str:
    hints = source.get("capture_type_hints", {})
    text_lower = (text[:600]).lower()
    for trigger in hints.get("voice_note_triggers", ["rb note", "rb,", "note to self"]):
        if trigger in text_lower:
            return "voice_note"
    for trigger in hints.get("walking_triggers", ["driving", "walking", "in the car"]):
        if trigger in text_lower:
            return "walking"
    if any(w in text_lower for w in ("keynote", "panel", "breakout", "conference session")):
        return "conference"
    if any(w in text_lower for w in ("call", "phone")) and len(text.split()) < 300:
        return "phone_call"
    return "meeting"


# ---------------------------------------------------------------------------
# Pending queue writer
# ---------------------------------------------------------------------------

def _pending_path(file_id: str) -> Path:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    return PENDING_DIR / f"{file_id}.json"


def _write_pending(
    file_id: str,
    source: dict,
    transcript: str,
    transcription_method: str,
    queued_at: str,
    *,
    path: Path | None = None,
    title_hint: str | None = None,
    source_file_ref: str | None = None,
    extra: dict | None = None,
    capture_type: str | None = None,
) -> Path:
    """Write a pending capture JSON file. Returns the path written.

    `path` is set for filesystem-sourced captures (folder sweep, manual queue) and
    is used to derive a fallback title/source_file when the caller doesn't supply
    `title_hint`/`source_file_ref` directly — API-sourced captures (e.g. Granola)
    have no filesystem path and pass those explicitly instead.

    `capture_type` — RB-2026-09-11: when the caller already knows the real
    type (e.g. queueCaptureText always queues a pasted article/note, never a
    conversation), pass it directly rather than falling through
    _detect_capture_type()'s keyword triggers, whose fallback is "meeting" --
    correct for an undetected phone call, wrong for a pasted article.
    """
    if capture_type is None:
        capture_type = _detect_capture_type(transcript, source)

    if title_hint is None:
        # Infer title from filename: "06-00-33" → use source label + date context
        # A better title comes from the GPT during processing
        raw_stem = path.stem  # e.g. "06-00-33" or "Recording 2026-07-01 at 06.00"
        parent_name = path.parent.name  # often YYYY-MM-DD for JPR
        title_hint = f"{source.get('label','Capture')} — {parent_name} {raw_stem}".strip(" —")

    payload: dict[str, Any] = {
        "file_id": file_id,
        "queued_at": queued_at,
        "source_id": source.get("id"),
        "source_label": source.get("label"),
        "source_file": source_file_ref if source_file_ref is not None else str(path),
        "capture_type": capture_type,
        "title_hint": title_hint,
        "transcription_method": transcription_method,
        "transcript": transcript,
        "word_count": len(transcript.split()) if transcript else 0,
        "transcript_available": bool(transcript),
        "status": "pending",
    }
    if extra:
        payload["source_metadata"] = extra

    out = _pending_path(file_id)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Core sweep
# ---------------------------------------------------------------------------

def sweep(source_filter: str | None = None, *, dry_run: bool = False) -> dict:
    """Sweep all enabled sources for new files and queue them as pending."""
    sources = _load_sources(source_filter)
    registry = _load_registry()
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=_sweep_window_hours())
    queued_at = now.isoformat(timespec="seconds")

    results = []
    total_queued = 0
    total_skipped = 0
    total_errors = 0

    for source in sources:
        source_id = source.get("id", "unknown")
        try:
            new_files = _find_new_files(source, registry, since)
        except Exception as exc:
            results.append({"source": source_id, "status": "error", "error": str(exc)})
            total_errors += 1
            continue

        for path in new_files:
            file_id = _file_id(path)
            try:
                transcript, method = _extract_text(path, source.get("transcription", "pre_transcribed"))

                if dry_run:
                    results.append({
                        "source": source_id,
                        "status": "dry_run",
                        "file": str(path),
                        "file_id": file_id,
                        "word_count": len(transcript.split()) if transcript else 0,
                        "transcription_method": method,
                        "transcript_available": bool(transcript),
                    })
                    total_queued += 1
                    continue

                pending_path = _write_pending(
                    file_id,
                    source,
                    transcript,
                    method,
                    queued_at,
                    path=path,
                    # A source's own explicit capture_type always wins. Absent
                    # that, an OCR'd image or extracted document defaults to
                    # "pasted_content" (an article/post/document, not a
                    # meeting) rather than falling through to
                    # _detect_capture_type()'s keyword triggers, whose own
                    # fallback ("meeting") was only ever tuned for audio/text
                    # transcripts.
                    capture_type=source.get("capture_type") or ("pasted_content" if method in _NON_MEETING_METHODS else None),
                )

                registry["processed_ids"][file_id] = {
                    "queued_at": queued_at,
                    "source_id": source_id,
                    "source_file": str(path),
                    "pending_path": str(pending_path),
                    "status": "pending",
                }

                audit_log.append_event(
                    event_type="item_persisted",
                    item_summary=f"Capture queued: {path.name} [{source_id}]",
                    reason="capture_ingest_sweep",
                    outcome=f"pending transcript_available={bool(transcript)} method={method}",
                    data_class="raw_source",
                    source=f"capture_ingest:{source_id}",
                )

                results.append({
                    "source": source_id,
                    "status": "queued",
                    "file": str(path),
                    "file_id": file_id,
                    "pending": str(pending_path.relative_to(SYSTEM_DIR.parent)),
                    "word_count": len(transcript.split()) if transcript else 0,
                    "transcription_method": method,
                    "transcript_available": bool(transcript),
                })
                total_queued += 1

            except Exception as exc:
                results.append({
                    "source": source_id,
                    "status": "error",
                    "file": str(path),
                    "error": str(exc),
                })
                total_errors += 1

    if not dry_run:
        registry["last_scan"] = queued_at
        _save_registry(registry)

    return {
        "swept_at": queued_at,
        "since": since.isoformat(timespec="seconds"),
        "sources_checked": len(sources),
        "files_queued": total_queued,
        "files_skipped": total_skipped,
        "errors": total_errors,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Pending queue inspection
# ---------------------------------------------------------------------------

def list_pending(limit: int = 20) -> list[dict]:
    """Return pending captures awaiting GPT processing, newest first."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(PENDING_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Omit full transcript from listing — GPT fetches via API
            items.append({
                "file_id": data.get("file_id"),
                "queued_at": data.get("queued_at"),
                "source_label": data.get("source_label"),
                "capture_type": data.get("capture_type"),
                "title_hint": data.get("title_hint"),
                "word_count": data.get("word_count", 0),
                "transcript_available": data.get("transcript_available", False),
                "transcription_method": data.get("transcription_method"),
                "status": data.get("status", "pending"),
            })
        except (json.JSONDecodeError, OSError):
            continue
        if len(items) >= limit:
            break
    return items


def get_pending(file_id: str) -> dict | None:
    """Return full pending capture payload including transcript."""
    path = _pending_path(file_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_processed(limit: int = 20, since_date: str | None = None) -> list[dict]:
    """Return processed captures, newest first -- real gap found live
    2026-08-27: nothing in this module (or the chat surface built on top of
    it) could ever look BACK at a capture once mark_processed() moved it out
    of pending/. A user asking to review/summarize "today's calls" after
    they were already processed had no way to retrieve them again --
    getCapturesPending correctly reported nothing pending, which read as
    "nothing exists" even though real, already-processed content did.

    since_date: "today", "yesterday", or an explicit "YYYY-MM-DD" -- only
    captures with queued_at on or after that date. Mirrors list_pending's
    field shape, plus processed_at.

    "today"/"yesterday" resolved HERE, server-side -- not left to the
    caller to compute. Real gap found live 2026-08-27: even with the
    correct current date freshly injected into the model's instructions
    every turn, gpt-4o-mini still could not reliably compute today's date
    as an ISO string for this parameter (it produced "2023-10-09", then
    "2023-10-27" on a retry -- a stale year/month with the real day
    grafted on, twice). Same principle as everywhere else in this system:
    never make the model construct a precise value it might get wrong --
    give it a literal to relay, and resolve the real value with real code.

    A second real bug found immediately after fixing the first: this
    machine is in Central time, 5-6 hours behind UTC, and queued_at is
    stored in UTC. Computing "today" via datetime.now(timezone.utc) at
    8:27pm CDT resolved to the UTC date that had already rolled over to
    tomorrow, silently excluding every real capture from today's actual
    local calendar day (0 results, confirmed live). "Today" has to mean
    the user's local calendar day -- computed in local time, then
    converted to a UTC instant for comparison against queued_at.
    """
    since_start_utc = None
    if since_date in ("today", "yesterday"):
        local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        if since_date == "yesterday":
            local_midnight -= timedelta(days=1)
        since_start_utc = local_midnight.astimezone(timezone.utc)
    elif since_date:
        try:
            naive_midnight = datetime.strptime(since_date, "%Y-%m-%d")
            since_start_utc = naive_midnight.astimezone().astimezone(timezone.utc)
        except ValueError:
            since_start_utc = None  # malformed input -- don't filter rather than silently drop everything

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(PROCESSED_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if since_start_utc is not None:
            try:
                queued_at_dt = datetime.fromisoformat(str(data.get("queued_at")))
                if queued_at_dt.tzinfo is None:
                    queued_at_dt = queued_at_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if queued_at_dt < since_start_utc:
                continue
        items.append({
            "file_id": data.get("file_id"),
            "queued_at": data.get("queued_at"),
            "processed_at": data.get("processed_at"),
            "source_label": data.get("source_label"),
            "capture_type": data.get("capture_type"),
            "title_hint": data.get("title_hint"),
            "word_count": data.get("word_count", 0),
            "transcript_available": data.get("transcript_available", False),
            "processing_result": data.get("processing_result"),
            "source_file": data.get("source_file"),
        })
        if len(items) >= limit:
            break
    return items


def get_processed(file_id: str) -> dict | None:
    """Return full processed-capture payload including transcript and the
    triage result recorded when it was processed."""
    path = PROCESSED_DIR / f"{file_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def deep_research_sidecar_info(source_file: str | None) -> dict:
    """RB defect 2026-09-30: a deep-research capture's Markdown transcript
    and its structured JSON sidecar share the same basename in the drop
    folder, but nothing linked a capture's file_id to its sidecar's
    packet_id -- the capture receipt (exec_mutations only) and the
    sidecar's own structured-import outcome were two disconnected numbers.
    Returns {"exists", "has_findings", "packet_id", "path"} so a
    reconciliation step can look up the importer's receipt for this
    capture's packet_id after the fact."""
    empty = {"exists": False, "has_findings": False, "packet_id": None, "path": None}
    if not source_file:
        return empty
    md_path = Path(source_file)
    sidecar = md_path.with_suffix(".json")
    if not sidecar.exists():
        return empty
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"exists": True, "has_findings": False, "packet_id": None, "path": str(sidecar)}
    findings = data.get("findings")
    return {
        "exists": True,
        "has_findings": bool(isinstance(findings, list) and findings),
        "packet_id": data.get("packet_id"),
        "path": str(sidecar),
    }


def reconcile_processing_result(file_id: str, updates: dict) -> bool:
    """Merge `updates` into an already-processed capture's stored
    processing_result, for a downstream pipeline step (the structured
    deep-research importer) that runs after mark_processed() already wrote
    its own receipt. Only adds/overwrites the given keys -- never touches
    anything else in the capture record. Returns False if the capture
    doesn't exist or was never marked processed."""
    path = PROCESSED_DIR / f"{file_id}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    result = data.get("processing_result")
    if not isinstance(result, dict):
        return False
    result.update(updates)
    data["processing_result"] = result
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return True


def mark_processed(file_id: str, result: dict | None = None) -> bool:
    """Move a pending capture to processed/ after GPT has handled it."""
    src = _pending_path(file_id)
    if not src.exists():
        return False
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    dst = PROCESSED_DIR / src.name
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        data["status"] = "processed"
        data["processed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if result:
            data["processing_result"] = result
        dst.write_text(json.dumps(data, indent=2), encoding="utf-8")
        src.unlink()

        # Update registry status
        reg = _load_registry()
        if file_id in reg.get("processed_ids", {}):
            reg["processed_ids"][file_id]["status"] = "processed"
            reg["processed_ids"][file_id]["processed_at"] = data["processed_at"]
            _save_registry(reg)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Manual queue
# ---------------------------------------------------------------------------

def queue_file(path: Path, source_id: str = "custom", transcription_mode: str | None = None) -> dict:
    """Manually queue any transcript file regardless of source config.

    transcription_mode overrides the source's configured mode -- needed for
    a raw audio file queued from outside any folder-watch source (e.g. a
    chat upload), where _load_sources() has no entry to read a mode from and
    would otherwise fall back to "pre_transcribed" (read-as-text), which is
    wrong for audio bytes.
    """
    sources = _load_sources()
    source = next((s for s in sources if s.get("id") == source_id), {
        "id": source_id,
        "label": "Manual",
        "transcription": "pre_transcribed",
        "capture_type_hints": {},
    })
    registry = _load_registry()
    file_id = _file_id(path)
    if file_id in registry.get("processed_ids", {}):
        return {"status": "already_queued", "file_id": file_id}

    mode = transcription_mode or source.get("transcription", "pre_transcribed")
    transcript, method = _extract_text(path, mode)
    queued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Same non-meeting -> pasted_content default as sweep() above -- a
    # manually-queued screenshot or document shouldn't fall through to
    # _detect_capture_type()'s "meeting" fallback either.
    capture_type = source.get("capture_type") or ("pasted_content" if method in _NON_MEETING_METHODS else None)
    pending_path = _write_pending(file_id, source, transcript, method, queued_at, path=path, capture_type=capture_type)
    registry["processed_ids"][file_id] = {
        "queued_at": queued_at,
        "source_id": source_id,
        "source_file": str(path),
        "pending_path": str(pending_path),
        "status": "pending",
    }
    _save_registry(registry)
    return {
        "status": "queued",
        "file_id": file_id,
        "pending": str(pending_path.relative_to(SYSTEM_DIR.parent)),
        "word_count": len(transcript.split()) if transcript else 0,
        "transcript_available": bool(transcript),
    }


def queue_text(
    text: str,
    *,
    source_id: str,
    source_label: str,
    external_id: str,
    title_hint: str,
    capture_type_hints: dict | None = None,
    extra: dict | None = None,
    capture_type: str | None = None,
) -> dict:
    """Queue a capture whose transcript arrived as text from an external API
    (no local file) — e.g. a cloud notetaker like Granola. `external_id` must be
    a stable identifier from the origin system; it's the dedup key in place of
    a filesystem path.

    `capture_type` — see _write_pending()'s docstring. Pass explicitly for a
    caller that already knows the real type (e.g. queueCaptureText's pasted
    articles/notes, always "pasted_content", never keyword-detected).
    """
    registry = _load_registry()
    file_id = _file_id(f"{source_id}:{external_id}")
    if file_id in registry.get("processed_ids", {}):
        return {"status": "already_queued", "file_id": file_id}

    source = {
        "id": source_id,
        "label": source_label,
        "capture_type_hints": capture_type_hints or {},
    }
    queued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source_file_ref = f"{source_id}:{external_id}"
    pending_path = _write_pending(
        file_id, source, text, "pre_transcribed", queued_at,
        title_hint=title_hint, source_file_ref=source_file_ref, extra=extra,
        capture_type=capture_type,
    )

    registry["processed_ids"][file_id] = {
        "queued_at": queued_at,
        "source_id": source_id,
        "source_file": source_file_ref,
        "pending_path": str(pending_path),
        "status": "pending",
    }
    _save_registry(registry)

    audit_log.append_event(
        event_type="item_persisted",
        item_summary=f"Capture queued: {title_hint} [{source_id}]",
        reason="capture_ingest_queue_text",
        outcome=f"pending transcript_available={bool(text)}",
        data_class="raw_source",
        source=f"capture_ingest:{source_id}",
    )

    try:
        pending_display = str(pending_path.relative_to(SYSTEM_DIR.parent))
    except ValueError:
        pending_display = str(pending_path)

    return {
        "status": "queued",
        "file_id": file_id,
        "pending": pending_display,
        "word_count": len(text.split()) if text else 0,
        "transcript_available": bool(text),
    }


# ---------------------------------------------------------------------------
# Daily brief summary (called by render_daily_brief.py)
# ---------------------------------------------------------------------------

def pending_brief_summary() -> dict | None:
    """Return a brief-ready summary of captures pending processing.

    Returns None if nothing is pending.
    Called by render_daily_brief.py to populate the Captures section.
    """
    items = list_pending(limit=50)
    if not items:
        return None

    by_type: dict[str, int] = {}
    no_transcript = 0
    for item in items:
        ct = item.get("capture_type", "unknown")
        by_type[ct] = by_type.get(ct, 0) + 1
        if not item.get("transcript_available"):
            no_transcript += 1

    return {
        "pending_count": len(items),
        "by_type": by_type,
        "no_transcript_count": no_transcript,
        "captures": items,
        "action": "Call /captures/pending to retrieve transcripts, then /captures/{id}/submit to process each one.",
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    import tempfile
    errors = []

    # File ID stability
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"hello world test")
        tmp = Path(f.name)
    fid1, fid2 = _file_id(tmp), _file_id(tmp)
    tmp.unlink()
    if fid1 != fid2:
        errors.append("file_id not stable")
    if not fid1.startswith("cap-"):
        errors.append("file_id prefix wrong")

    # Capture type detection
    jpr_source = {"capture_type_hints": {"voice_note_triggers": ["rb note"], "walking_triggers": ["driving"]}}
    assert _detect_capture_type("RB note, quick idea", jpr_source) == "voice_note"
    assert _detect_capture_type("driving to the airport", jpr_source) == "walking"
    assert _detect_capture_type("let's talk about the deal", jpr_source) == "meeting"

    # VTT stripping
    vtt = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nHello world\n\n2\n00:00:02.000 --> 00:00:04.000\nHow are you"
    with tempfile.NamedTemporaryFile(suffix=".vtt", delete=False, mode="w") as f:
        f.write(vtt)
        vtt_path = Path(f.name)
    text, method = _extract_text(vtt_path, "pre_transcribed")
    vtt_path.unlink()
    if "Hello world" not in text:
        errors.append(f"VTT stripping failed: {text!r}")
    if method != "pre_transcribed":
        errors.append("VTT method wrong")

    # Registry round-trip
    with tempfile.TemporaryDirectory() as td:
        reg = {"processed_ids": {"cap-abc": {"status": "pending"}}, "last_scan": None}
        rp = Path(td) / ".registry.json"
        rp.write_text(json.dumps(reg), encoding="utf-8")
        loaded = json.loads(rp.read_text(encoding="utf-8"))
        if loaded != reg:
            errors.append("registry round-trip failed")

    # Settings loader doesn't crash
    try:
        _load_sources()
    except Exception as exc:
        errors.append(f"_load_sources raised: {exc}")

    # queue_text — API-sourced captures with no filesystem path (e.g. Granola).
    # Redirect module-level dirs to a scratch tempdir so this doesn't touch
    # the real pending queue / registry.
    global PENDING_DIR, REGISTRY_PATH
    orig_pending_dir, orig_registry_path = PENDING_DIR, REGISTRY_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            PENDING_DIR = Path(td) / "pending"
            REGISTRY_PATH = Path(td) / ".registry.json"

            result1 = queue_text(
                "Hello from a meeting transcript.",
                source_id="granola", source_label="Granola",
                external_id="not_abc123", title_hint="Test meeting",
                extra={"web_url": "https://notes.granola.ai/d/not_abc123"},
            )
            if result1.get("status") != "queued":
                errors.append(f"queue_text first call not queued: {result1}")

            result2 = queue_text(
                "Hello from a meeting transcript.",
                source_id="granola", source_label="Granola",
                external_id="not_abc123", title_hint="Test meeting",
            )
            if result2.get("status") != "already_queued":
                errors.append(f"queue_text dedup failed: {result2}")
            if result1.get("file_id") != result2.get("file_id"):
                errors.append("queue_text dedup key not stable")

            written = json.loads((PENDING_DIR / f"{result1['file_id']}.json").read_text(encoding="utf-8"))
            if written.get("source_metadata", {}).get("web_url") != "https://notes.granola.ai/d/not_abc123":
                errors.append("queue_text extra metadata not persisted")
            if written.get("title_hint") != "Test meeting":
                errors.append("queue_text title_hint override not used")
    finally:
        PENDING_DIR, REGISTRY_PATH = orig_pending_dir, orig_registry_path

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False

    print("capture_ingest smoke: all checks passed")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RB ICS — source sweeper")
    parser.add_argument("--scan", action="store_true", help="Sweep all enabled sources for new captures")
    parser.add_argument("--source", metavar="ID", help="Limit --scan to a specific source ID")
    parser.add_argument("--dry-run", action="store_true", help="Detect files but don't write pending queue")
    parser.add_argument("--pending", action="store_true", help="List captures awaiting processing")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--queue", metavar="FILE", help="Manually queue a transcript file")
    parser.add_argument("--queue-source", metavar="SOURCE_ID", default="custom",
                        help="Source ID to use with --queue (default: custom)")
    parser.add_argument("--smoke", action="store_true", help="Run smoke tests")
    args = parser.parse_args()

    if args.smoke:
        ok = _smoke()
        sys.exit(0 if ok else 1)

    if args.pending:
        print(json.dumps(list_pending(limit=args.limit), indent=2))
        return

    if args.queue:
        path = Path(args.queue).expanduser().resolve()
        if not path.exists():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(queue_file(path, source_id=args.queue_source), indent=2))
        return

    if args.scan:
        result = sweep(source_filter=args.source, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        sys.exit(1 if result.get("errors", 0) > 0 else 0)

    parser.print_help()


if __name__ == "__main__":
    main()
