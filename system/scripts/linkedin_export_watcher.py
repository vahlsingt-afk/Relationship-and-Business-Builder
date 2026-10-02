#!/usr/bin/env python3
"""
linkedin_export_watcher.py — Semi-automated LinkedIn export ingest watcher.

Watches drop locations for LinkedIn export ZIPs and message CSVs, routes
them through the appropriate existing ingester, and maintains a hash manifest
so files are never reprocessed unless --force is given.

Drop locations watched:
    system/inbox/linkedin_exports/       (ZIPs and standalone CSVs)
    system/inbox/linkedin_messages_export.csv   (convenience single-file path)

Routing:
    ZIP containing messages.csv   → linkedin_messaging.py --ingest ... --confirm
    Full connections/archive ZIP  → linkedin_ingest.py <path> [--dry-run]
    Standalone messages.csv       → linkedin_messaging.py --ingest ... --confirm

Hash manifest:
    system/.cache/linkedin_export_watcher.json

CLI:
    python3 linkedin_export_watcher.py --scan
    python3 linkedin_export_watcher.py --ingest-new --dry-run
    python3 linkedin_export_watcher.py --ingest-new --confirm
    python3 linkedin_export_watcher.py --smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXPORTS_DIR = core.INBOX_DIR / "linkedin_exports"
MESSAGES_EXPORT_PATH = core.INBOX_DIR / "linkedin_messages_export.csv"
MANIFEST_PATH = core.CACHE_DIR / "linkedin_export_watcher.json"

SCRIPTS_DIR = Path(__file__).resolve().parent
MESSAGING_SCRIPT = SCRIPTS_DIR / "linkedin_messaging.py"
INGEST_SCRIPT = SCRIPTS_DIR / "linkedin_ingest.py"

PY = sys.executable


# ---------------------------------------------------------------------------
# Hash manifest
# ---------------------------------------------------------------------------

def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"processed": {}}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"processed": {}}


def _save_manifest(manifest: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

def _classify_file(path: Path) -> str:
    """Return one of: messages_csv | linkedin_zip_messages | linkedin_zip_connections | unknown."""
    if path.suffix.lower() == ".csv":
        # Check if it looks like a LinkedIn messages CSV
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            first_line = text.split("\n", 1)[0].lower()
            if any(k in first_line for k in ("conversation id", "from", "content", "folder")):
                return "messages_csv"
        except Exception:  # noqa: BLE001
            pass
        return "messages_csv"  # assume CSV = messages

    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                names_lower = [n.lower() for n in zf.namelist()]
                has_messages = any("messages.csv" in n for n in names_lower)
                has_connections = any("connections.csv" in n for n in names_lower)
                if has_messages and not has_connections:
                    return "linkedin_zip_messages"
                if has_connections:
                    return "linkedin_zip_connections"
                if has_messages:
                    return "linkedin_zip_messages"
        except zipfile.BadZipFile:
            return "unknown"
        return "unknown"

    return "unknown"


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def _rel_path(path: Path) -> str:
    try:
        return str(path.relative_to(core.PROJECT_DIR))
    except ValueError:
        return str(path)


def scan() -> dict[str, Any]:
    """Enumerate candidate files and classify them."""
    manifest = _load_manifest()
    processed = manifest.get("processed") or {}
    candidates: list[dict[str, Any]] = []

    # Drop dir
    if EXPORTS_DIR.exists():
        for path in sorted(EXPORTS_DIR.iterdir()):
            if path.is_file() and path.suffix.lower() in (".zip", ".csv"):
                file_hash = _file_hash(path)
                classification = _classify_file(path)
                candidates.append({
                    "path": str(path),
                    "rel_path": _rel_path(path),
                    "hash": file_hash,
                    "classification": classification,
                    "already_processed": file_hash in processed,
                    "processed_at": processed.get(file_hash, {}).get("processed_at"),
                })

    # Convenience single-file path
    if MESSAGES_EXPORT_PATH.exists():
        file_hash = _file_hash(MESSAGES_EXPORT_PATH)
        candidates.append({
            "path": str(MESSAGES_EXPORT_PATH),
            "rel_path": _rel_path(MESSAGES_EXPORT_PATH),
            "hash": file_hash,
            "classification": "messages_csv",
            "already_processed": file_hash in processed,
            "processed_at": processed.get(file_hash, {}).get("processed_at"),
        })

    new_count = sum(1 for c in candidates if not c["already_processed"])
    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(core.PROJECT_DIR))
        except ValueError:
            return str(p)

    return {
        "ok": True,
        "candidates": candidates,
        "total": len(candidates),
        "new": new_count,
        "already_processed": len(candidates) - new_count,
        "manifest_path": _rel(MANIFEST_PATH),
        "exports_dir": _rel(EXPORTS_DIR),
        "full_archive_ingest_available": INGEST_SCRIPT.exists(),
        "full_archive_ingest_note": (
            None if INGEST_SCRIPT.exists()
            else "linkedin_ingest.py not found — full-archive (Connections.csv) ZIPs will fail. "
                 "Messages-only ZIPs and CSV files are unaffected."
        ),
    }


# ---------------------------------------------------------------------------
# Ingest routing
# ---------------------------------------------------------------------------

_MAX_EXTRACTED_CSV_BYTES = 500 * 1024 * 1024  # RB-SECURITY-2026-09-03: decompression-bomb guard


def _extract_messages_csv_from_zip(zip_path: Path) -> Path:
    """Extract messages.csv from ZIP into a temp file. Caller must clean up."""
    tmpdir = tempfile.mkdtemp(prefix="rb_li_export_")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        msg_name = next(n for n in names if "messages.csv" in n.lower())
        info = zf.getinfo(msg_name)
        if info.file_size > _MAX_EXTRACTED_CSV_BYTES:
            raise ValueError(
                f"{msg_name} would decompress to {info.file_size:,} bytes, over the "
                f"{_MAX_EXTRACTED_CSV_BYTES:,}-byte limit — refusing to extract (possible decompression bomb)."
            )
        zf.extract(msg_name, path=tmpdir)
        extracted = Path(tmpdir) / msg_name
    return extracted


def _run_messaging_ingest(csv_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """Route a messages CSV through linkedin_messaging.py."""
    cmd = [PY, str(MESSAGING_SCRIPT), "--ingest", str(csv_path)]
    if dry_run:
        pass  # preview mode (default)
    else:
        cmd.append("--confirm")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "router": "linkedin_messaging.py",
            "stdout": stdout[:2000] if stdout else "",
            "stderr": stderr[:500] if stderr else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "router": "linkedin_messaging.py", "error": str(exc)}


def _run_connections_ingest(zip_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """Route a full LinkedIn archive ZIP through linkedin_ingest.py."""
    if not INGEST_SCRIPT.exists():
        return {
            "ok": False,
            "router": "linkedin_ingest.py",
            "status": "script_not_found",
            "error": (
                "system/scripts/linkedin_ingest.py is not present in this checkout. "
                "Commit or restore that file to enable full-archive (Connections.csv) ingest. "
                "Messages-only ZIPs and standalone CSVs route through linkedin_messaging.py "
                "and are unaffected."
            ),
        }
    cmd = [PY, str(INGEST_SCRIPT), str(zip_path)]
    if dry_run:
        cmd.append("--dry-run")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "router": "linkedin_ingest.py",
            "stdout": stdout[:2000] if stdout else "",
            "stderr": stderr[:500] if stderr else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "router": "linkedin_ingest.py", "error": str(exc)}


def _process_candidate(candidate: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Process a single candidate file. Returns a result dict."""
    path = Path(candidate["path"])
    classification = candidate["classification"]
    tmp_csv: Path | None = None

    try:
        if classification == "messages_csv":
            result = _run_messaging_ingest(path, dry_run=dry_run)
        elif classification == "linkedin_zip_messages":
            tmp_csv = _extract_messages_csv_from_zip(path)
            result = _run_messaging_ingest(tmp_csv, dry_run=dry_run)
        elif classification == "linkedin_zip_connections":
            connections_result = _run_connections_ingest(path, dry_run=dry_run)
            messages_result = None
            try:
                with zipfile.ZipFile(path) as zf:
                    has_messages = any(
                        "messages.csv" in name.lower() for name in zf.namelist()
                    )
                if has_messages:
                    tmp_csv = _extract_messages_csv_from_zip(path)
                    messages_result = _run_messaging_ingest(tmp_csv, dry_run=dry_run)
            except (zipfile.BadZipFile, StopIteration) as exc:
                messages_result = {
                    "ok": False,
                    "router": "linkedin_messaging.py",
                    "error": str(exc),
                }

            result = {
                "ok": (
                    bool(connections_result.get("ok"))
                    and (
                        messages_result is None
                        or bool(messages_result.get("ok"))
                    )
                ),
                "router": "linkedin_full_archive",
                "connections_ingest": connections_result,
                "messages_ingest": messages_result,
            }
        else:
            result = {
                "ok": False,
                "router": None,
                "error": f"Unknown classification: {classification}",
            }
    finally:
        if tmp_csv and tmp_csv.parent.name.startswith("rb_li_export_"):
            import shutil
            shutil.rmtree(tmp_csv.parent, ignore_errors=True)

    return {
        "path": candidate["rel_path"],
        "hash": candidate["hash"],
        "classification": classification,
        "dry_run": dry_run,
        **result,
    }


def ingest_new(*, dry_run: bool, force: bool = False, trigger_cascade: bool = False) -> dict[str, Any]:
    """Ingest all unprocessed (or all if --force) candidates."""
    scan_result = scan()
    candidates = scan_result["candidates"]

    if not candidates:
        return {
            "ok": True,
            "message": "No candidate files found.",
            "processed": [],
            "skipped": [],
        }

    manifest = _load_manifest()
    processed_map = manifest.get("processed") or {}

    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for candidate in candidates:
        if candidate["already_processed"] and not force:
            skipped.append({
                "path": candidate["rel_path"],
                "hash": candidate["hash"],
                "reason": "already_processed",
                "processed_at": candidate.get("processed_at"),
            })
            continue

        if candidate["classification"] == "unknown":
            skipped.append({
                "path": candidate["rel_path"],
                "hash": candidate["hash"],
                "reason": "unknown_classification",
            })
            continue

        result = _process_candidate(candidate, dry_run=dry_run)
        results.append(result)

        # Record in manifest only on confirmed successful write
        if result.get("ok") and not dry_run:
            processed_map[candidate["hash"]] = {
                "path": candidate["rel_path"],
                "classification": candidate["classification"],
                "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }

    if not dry_run:
        manifest["processed"] = processed_map
        _save_manifest(manifest)

    ok_count = sum(1 for r in results if r.get("ok"))
    fail_count = len(results) - ok_count

    # RB-DEFECT-2026-09-18: same gap and same fix as outlook_gui_capture.py's
    # ingest_staged() -- a manual LinkedIn export ingestion used to stop at
    # "wrote the file," with meeting-prep/loops/the brief cache never told.
    # trigger_cascade defaults False (opted in by the CLI only) for the same
    # reason: this function's own unit tests shouldn't pay for a full
    # subprocess pipeline run.
    cascade_receipt = None
    if trigger_cascade and not dry_run and ok_count > 0:
        try:
            import post_capture_cascade
            cascade_receipt = post_capture_cascade.run_cascade(trigger="linkedin_manual_capture")
        except Exception as exc:  # noqa: BLE001
            cascade_receipt = {"ok": False, "error": f"cascade failed to run: {exc}"}

    return {
        "ok": fail_count == 0,
        "dry_run": dry_run,
        "processed": results,
        "skipped": skipped,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "skipped_count": len(skipped),
        "cascade": cascade_receipt,
    }


# ---------------------------------------------------------------------------
# Smoke test (no network, no real LinkedIn files)
# ---------------------------------------------------------------------------

def _smoke() -> int:
    import io
    import shutil
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK  " if cond else "FAIL"
        print(f"  {mark}  {msg}")
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Patch paths
        global EXPORTS_DIR, MESSAGES_EXPORT_PATH, MANIFEST_PATH
        orig_exports = EXPORTS_DIR
        orig_msg = MESSAGES_EXPORT_PATH
        orig_manifest = MANIFEST_PATH

        EXPORTS_DIR = tmp_path / "linkedin_exports"
        MESSAGES_EXPORT_PATH = tmp_path / "linkedin_messages_export.csv"
        MANIFEST_PATH = tmp_path / "linkedin_export_watcher.json"

        try:
            EXPORTS_DIR.mkdir(parents=True)

            # 1. Empty scan
            result = scan()
            ck(result["ok"] is True, "scan returns ok=true")
            ck(result["total"] == 0, "empty scan: 0 candidates")

            # 2. Drop a fake messages CSV
            fake_csv = EXPORTS_DIR / "messages.csv"
            fake_csv.write_text(
                "CONVERSATION ID,FROM,SENDER PROFILE URL,TO,DATE,SUBJECT,CONTENT,FOLDER\n"
                "c1,Alice,https://linkedin.com/in/alice,,2026-05-01 10:00:00 UTC,Hi,Hello,INBOX\n",
                encoding="utf-8",
            )
            result2 = scan()
            ck(result2["total"] == 1, "scan finds 1 candidate after CSV drop")
            ck(result2["new"] == 1, "scan: 1 new (not yet processed)")
            ck(result2["candidates"][0]["classification"] == "messages_csv",
               "CSV classified as messages_csv")

            # 3. Drop a fake ZIP with messages.csv inside
            zip_path = EXPORTS_DIR / "linkedin_messages.zip"
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr(
                    "messages.csv",
                    "CONVERSATION ID,FROM,SENDER PROFILE URL,TO,DATE,SUBJECT,CONTENT,FOLDER\n"
                    "c2,Bob,https://linkedin.com/in/bob,,2026-05-02 09:00:00 UTC,Re,Hey,INBOX\n",
                )
            zip_path.write_bytes(buf.getvalue())
            result3 = scan()
            ck(result3["total"] == 2, "scan finds 2 candidates after ZIP drop")
            zip_candidate = next(c for c in result3["candidates"] if c["path"].endswith(".zip"))
            ck(zip_candidate["classification"] == "linkedin_zip_messages",
               "ZIP with messages.csv classified as linkedin_zip_messages")

            # 4. Drop a fake connections ZIP
            zip_conn_path = EXPORTS_DIR / "linkedin_connections.zip"
            buf2 = io.BytesIO()
            with zipfile.ZipFile(buf2, "w") as zf:
                zf.writestr("Connections.csv", "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n")
                zf.writestr("messages.csv", "CONVERSATION ID,FROM\n")
            zip_conn_path.write_bytes(buf2.getvalue())
            result4 = scan()
            conn_candidate = next(c for c in result4["candidates"] if "connections" in c["path"])
            ck(conn_candidate["classification"] == "linkedin_zip_connections",
               "Connections ZIP classified as linkedin_zip_connections")

            # 5. Hash deduplication: process (dry-run) the CSV, record its hash, rescan
            csv_candidate = next(c for c in result4["candidates"] if c["path"].endswith("messages.csv"))
            csv_hash = csv_candidate["hash"]
            fake_manifest = {"processed": {
                csv_hash: {"path": csv_candidate["rel_path"], "processed_at": "2026-05-24T00:00:00+00:00"}
            }}
            MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
            MANIFEST_PATH.write_text(json.dumps(fake_manifest))
            result5 = scan()
            csv_cand_after = next(c for c in result5["candidates"] if c["path"].endswith("messages.csv"))
            ck(csv_cand_after["already_processed"] is True, "hash dedup: CSV marked already_processed")

            # 6. dry-run ingest produces no manifest write
            MANIFEST_PATH.unlink(missing_ok=True)
            # We can't fully route without real scripts, but we can confirm the
            # ingest_new function respects dry_run=True (no manifest written)
            # Use a mock by checking the code path: if messaging script is absent,
            # result.ok will be False but manifest should not be written
            ingest_result = ingest_new(dry_run=True)
            ck(isinstance(ingest_result, dict), "ingest_new returns dict")
            ck(ingest_result.get("dry_run") is True, "ingest_new dry_run flag preserved")
            ck(not MANIFEST_PATH.exists(), "dry-run: manifest NOT written")

            # 7. Manifest structure
            MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
            MANIFEST_PATH.write_text(json.dumps({"processed": {"abc123": {"processed_at": "2026-05-01T00:00:00+00:00"}}}))
            m = _load_manifest()
            ck("processed" in m, "manifest loads correctly")

        finally:
            EXPORTS_DIR = orig_exports
            MESSAGES_EXPORT_PATH = orig_msg
            MANIFEST_PATH = orig_manifest

    if failures:
        print(f"\nSmoke FAILED ({len(failures)} failures):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("linkedin_export_watcher smoke OK")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Watch for LinkedIn export drops and route to ingesters.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--scan", action="store_true",
                   help="Show detected candidate files and their status.")
    p.add_argument("--ingest-new", action="store_true",
                   help="Process unprocessed candidates.")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview without writing. Default for --ingest-new.")
    p.add_argument("--confirm", action="store_true",
                   help="Actually write caches and update manifest.")
    p.add_argument("--force", action="store_true",
                   help="Re-process already-processed files.")
    p.add_argument("--smoke", action="store_true",
                   help="Run smoke tests (no network, no real files required).")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    if args.scan:
        result = scan()
        print(json.dumps(result, indent=2))
        return 0

    if args.ingest_new:
        if not args.dry_run and not args.confirm:
            print("ERROR: specify --dry-run (preview) or --confirm (write)", file=sys.stderr)
            return 2
        dry_run = not args.confirm
        result = ingest_new(dry_run=dry_run, force=args.force, trigger_cascade=True)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
