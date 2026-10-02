#!/usr/bin/env python3
"""li_capture.py — One-command LinkedIn intelligence gathering workflow.

Runs after the daily brief to refresh LinkedIn data. Each step has a y/n
prompt; only stale steps need attention on any given day.

Steps:
  1. Export scan  — find new LinkedIn ZIPs in ~/Downloads, copy & ingest
  2. Messages     — extract and process messages.csv from export ZIPs
  3. Feed capture — browser JS → /tmp/li_feed.json → ingest
  4. Engagement   — own-post engagement JS capture (optional)
  5. Social cache — refresh_sources.py --social --save-health

Usage:
  python3 system/scripts/li_capture.py               # full interactive
  python3 system/scripts/li_capture.py --status       # source health + exit
  python3 system/scripts/li_capture.py --export-only  # steps 1+2 only
  python3 system/scripts/li_capture.py --feed-only    # step 3 only
  python3 system/scripts/li_capture.py --engagement-only  # step 4 only
  python3 system/scripts/li_capture.py --non-interactive  # skip browser steps
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402


# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
INBOX_EXPORTS_DIR = core.INBOX_DIR / "linkedin_exports"
DOWNLOADS_DIR = Path.home() / "Downloads"
SOURCE_HEALTH_PATH = core.CACHE_DIR / "source_health.json"
MESSAGES_MANIFEST_PATH = core.CACHE_DIR / "li_capture_messages.json"
WATCHER_MANIFEST_PATH = core.CACHE_DIR / "linkedin_export_watcher.json"
FEED_TMP_DEFAULT = Path("/tmp/li_feed.json")
ENGAGEMENT_TMP_DEFAULT = Path("/tmp/linkedin_engagement.json")

WATCHER_SCRIPT = SCRIPTS_DIR / "linkedin_export_watcher.py"
MESSAGING_SCRIPT = SCRIPTS_DIR / "linkedin_messaging.py"
READER_SCRIPT = SCRIPTS_DIR / "linkedin_session_reader.py"
REFRESH_SCRIPT = SCRIPTS_DIR / "refresh_sources.py"

PY = sys.executable

# ZIPs in ~/Downloads whose names start with these are LinkedIn exports
LI_ZIP_PREFIXES = (
    "Complete_LinkedInDataExport_",
    "Basic_LinkedInDataExport_",
    "LinkedIn_",
)


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def _b(t: str) -> str:
    return f"{BOLD}{t}{RESET}"


def _ok(t: str) -> str:
    return f"{GREEN}✓{RESET} {t}"


def _warn(t: str) -> str:
    return f"{YELLOW}⚠{RESET}  {t}"


def _err(t: str) -> str:
    return f"{RED}✗{RESET} {t}"


def _arrow(t: str) -> str:
    return f"{CYAN}→{RESET} {t}"


def _sep(title: str = "") -> None:
    width = 58
    if title:
        filler = "─" * (width - len(title) - 5)
        print(f"\n{BOLD}─── {title}{RESET} {DIM}{filler}{RESET}")
    else:
        print(f"{DIM}{'─' * width}{RESET}")


def _ask(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"  {prompt} {hint}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not ans:
        return default
    return ans.startswith("y")


def _ask_str(prompt: str, default: str = "") -> str:
    hint = f"[{default}]" if default else ""
    try:
        ans = input(f"  {prompt} {hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return ans if ans else default


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _has_messages_csv(zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return any("messages.csv" in n.lower() for n in zf.namelist())
    except Exception:  # noqa: BLE001
        return False


_MAX_EXTRACTED_CSV_BYTES = 500 * 1024 * 1024  # RB-SECURITY-2026-09-03: decompression-bomb guard


def _extract_messages_csv(zip_path: Path, dest_dir: Path) -> Path | None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            msg_name = next((n for n in names if "messages.csv" in n.lower()), None)
            if not msg_name:
                return None
            info = zf.getinfo(msg_name)
            if info.file_size > _MAX_EXTRACTED_CSV_BYTES:
                raise ValueError(
                    f"{msg_name} would decompress to {info.file_size:,} bytes, over the "
                    f"{_MAX_EXTRACTED_CSV_BYTES:,}-byte limit — refusing to extract (possible decompression bomb)."
                )
            zf.extract(msg_name, path=dest_dir)
            extracted = dest_dir / msg_name
            # If extracted inside a subdirectory, flatten it
            if not extracted.exists():
                for candidate in dest_dir.rglob("messages.csv"):
                    extracted = candidate
                    break
            return extracted if extracted.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _try_clipboard(text: str) -> bool:
    """Copy text to clipboard via pbcopy (macOS). Returns True on success."""
    try:
        proc = subprocess.run(
            ["pbcopy"], input=text.encode(), timeout=5, check=False
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Source health
# ---------------------------------------------------------------------------

LI_SOURCES = [
    "social_feed",
    "social_engagement",
    "social_own_posts",
    "linkedin_messaging",
]


def _load_health() -> dict[str, Any]:
    return _load_json(SOURCE_HEALTH_PATH, {})


def _health_rows() -> list[tuple[str, str, str]]:
    """(key, status, last_refreshed_display) for each LI source."""
    health = _load_health()
    sources = health.get("sources", {})
    out = []
    for key in LI_SOURCES:
        entry = sources.get(key, {})
        status = entry.get("status", "unknown")
        last_raw = entry.get("last_refreshed_at") or ""
        if last_raw:
            try:
                dt = datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
                last = dt.strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                last = last_raw[:10]
        else:
            last = "never"
        out.append((key, status, last))
    return out


def _any_li_stale() -> bool:
    return any(s in ("stale", "skipped_no_raw_input", "unknown") for _, s, _ in _health_rows())


def _print_health() -> None:
    print(f"\n  {_b('LinkedIn source health:')}")
    for key, status, last in _health_rows():
        label = f"{key:<22}"
        if status == "refreshed":
            line = _ok(f"{label} refreshed   ({last})")
        elif status == "stale":
            line = _warn(f"{label} STALE       (last: {last})")
        elif status == "skipped_no_raw_input":
            line = _warn(f"{label} no input    (never processed)")
        else:
            line = f"  {label} {status}  ({last})"
        print(f"    {line}")


# ---------------------------------------------------------------------------
# Step 1: Export scan — Downloads → inbox
# ---------------------------------------------------------------------------

def _find_downloads_zips() -> list[Path]:
    if not DOWNLOADS_DIR.exists():
        return []
    return sorted(
        p for p in DOWNLOADS_DIR.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".zip"
        and any(p.name.startswith(prefix) for prefix in LI_ZIP_PREFIXES)
    )


def _watcher_processed_hashes() -> set[str]:
    manifest = _load_json(WATCHER_MANIFEST_PATH, {"processed": {}})
    return set((manifest.get("processed") or {}).keys())


def step_export_scan(*, non_interactive: bool = False) -> dict[str, Any]:
    _sep("Step 1: LinkedIn Export ZIPs")

    downloads_zips = _find_downloads_zips()
    if not downloads_zips:
        print(f"    {DIM}No LinkedIn export ZIPs found in ~/Downloads.{RESET}")
        return {"skipped": True, "reason": "no_downloads_found"}

    processed_hashes = _watcher_processed_hashes()
    new_zips = [p for p in downloads_zips if _file_hash(p) not in processed_hashes]
    done_zips = [p for p in downloads_zips if _file_hash(p) in processed_hashes]

    print(f"  Found {len(downloads_zips)} LinkedIn ZIP(s) in ~/Downloads:")
    for p in new_zips:
        print(f"    {GREEN}[new]{RESET}   {p.name}")
    for p in done_zips:
        print(f"    {DIM}[done]  {p.name}{RESET}")

    if not new_zips:
        print(f"    {_ok('All export ZIPs already processed.')}")
        return {"skipped": True, "reason": "all_already_processed"}

    do_copy = True if non_interactive else _ask(
        f"Copy {len(new_zips)} new ZIP(s) to inbox and ingest?", default=True
    )
    if not do_copy:
        print("    Skipped.")
        return {"skipped": True, "reason": "user_skipped"}

    INBOX_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    for src in new_zips:
        dest = INBOX_EXPORTS_DIR / src.name
        if dest.exists():
            print(f"    {_arrow(src.name + ' already in inbox — skipping copy')}")
        else:
            print(f"    {_arrow(f'Copying {src.name}...')}", end="  ", flush=True)
            shutil.copy2(src, dest)
            print("done")

    print(f"    {_arrow('Running export watcher (connections + baseline)...')}", end="  ", flush=True)
    rc, stdout, stderr = _run([PY, str(WATCHER_SCRIPT), "--ingest-new", "--confirm"])
    if rc == 0:
        try:
            data = json.loads(stdout)
            ok_n = data.get("ok_count", 0)
            fail_n = data.get("fail_count", 0)
            tag = f"{ok_n} ingested" + (f", {fail_n} failed" if fail_n else "")
            print(_ok(tag))
        except Exception:  # noqa: BLE001
            print(_ok("done"))
    else:
        print(_err(f"watcher returned rc={rc}"))
        if stderr:
            print(f"    {DIM}{stderr[:200]}{RESET}")

    return {
        "skipped": False,
        "new_zips": [p.name for p in new_zips],
        "watcher_rc": rc,
    }


# ---------------------------------------------------------------------------
# Step 2: Messages ingest
# ---------------------------------------------------------------------------

def _messages_processed_hashes() -> set[str]:
    manifest = _load_json(MESSAGES_MANIFEST_PATH, {"processed": {}})
    return set((manifest.get("processed") or {}).keys())


def _watcher_messages_hashes() -> set[str]:
    """Hashes of ZIPs the watcher already routed as linkedin_zip_messages."""
    manifest = _load_json(WATCHER_MANIFEST_PATH, {"processed": {}})
    return {
        h for h, entry in (manifest.get("processed") or {}).items()
        if entry.get("classification") == "linkedin_zip_messages"
    }


def _record_messages_processed(zip_hash: str, zip_path: Path) -> None:
    manifest = _load_json(MESSAGES_MANIFEST_PATH, {"processed": {}})
    manifest.setdefault("processed", {})[zip_hash] = {
        "path": str(zip_path),
        "processed_at": _now_iso(),
    }
    _save_json(MESSAGES_MANIFEST_PATH, manifest)


def step_messages(*, non_interactive: bool = False) -> dict[str, Any]:
    _sep("Step 2: LinkedIn Messages")

    if not INBOX_EXPORTS_DIR.exists():
        print(f"    {DIM}No inbox/linkedin_exports/ yet — run Step 1 first.{RESET}")
        return {"skipped": True, "reason": "no_inbox_dir"}

    msg_done = _messages_processed_hashes() | _watcher_messages_hashes()

    candidates: list[tuple[Path, str, bool]] = []  # (path, hash, already_done)
    for path in sorted(INBOX_EXPORTS_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() == ".zip" and _has_messages_csv(path):
            h = _file_hash(path)
            candidates.append((path, h, h in msg_done))

    if not candidates:
        print(f"    {DIM}No ZIPs with messages.csv found in inbox.{RESET}")
        return {"skipped": True, "reason": "no_message_zips"}

    new_ones = [(p, h) for p, h, done in candidates if not done]
    done_ones = [p for p, h, done in candidates if done]

    print(f"  ZIPs containing messages.csv:")
    for p in new_ones:
        print(f"    {GREEN}[new]{RESET}   {p[0].name}")
    for p in done_ones:
        print(f"    {DIM}[done]  {p.name}{RESET}")

    if not new_ones:
        print(f"    {_ok('All message ZIPs already processed.')}")
        return {"skipped": True, "reason": "all_already_processed"}

    do_ingest = True if non_interactive else _ask(
        f"Process messages from {len(new_ones)} ZIP(s)?", default=True
    )
    if not do_ingest:
        print("    Skipped.")
        return {"skipped": True, "reason": "user_skipped"}

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rb_li_msg_") as tmp:
        tmp_path = Path(tmp)
        for zip_path, zip_hash in new_ones:
            print(f"    {_arrow(f'Extracting messages.csv from {zip_path.name}...')}", end="  ", flush=True)
            csv_path = _extract_messages_csv(zip_path, tmp_path)
            if not csv_path:
                print(_err("messages.csv not found"))
                results.append({"zip": zip_path.name, "ok": False, "error": "extraction_failed"})
                continue
            size_kb = csv_path.stat().st_size // 1024
            print(f"extracted ({size_kb:,} KB)")

            print(f"    {_arrow('Ingesting messages...')}", end="  ", flush=True)
            rc, stdout, stderr = _run(
                [PY, str(MESSAGING_SCRIPT), "--ingest", str(csv_path), "--confirm"],
                timeout=300,
            )
            if rc == 0:
                # Grab a one-line count hint from stdout
                hint = ""
                for line in stdout.splitlines():
                    lw = line.lower()
                    if any(k in lw for k in ("thread", "row", "message", "written")):
                        hint = line.strip()[:70]
                        break
                print(_ok(hint or "done"))
                _record_messages_processed(zip_hash, zip_path)
                results.append({"zip": zip_path.name, "ok": True})
            else:
                print(_err(f"rc={rc}"))
                if stderr:
                    print(f"    {DIM}{stderr[:200]}{RESET}")
                results.append({"zip": zip_path.name, "ok": False, "error": stderr[:120]})

    return {"skipped": False, "results": results}


# ---------------------------------------------------------------------------
# Step 3: Feed capture (browser JS)
# ---------------------------------------------------------------------------

def step_feed(*, non_interactive: bool = False) -> dict[str, Any]:
    _sep("Step 3: LinkedIn Feed Capture")

    if non_interactive:
        print(f"    {DIM}Feed capture requires a browser — skipping in non-interactive mode.{RESET}")
        print(f"    {DIM}Run separately: python3 system/scripts/li_capture.py --feed-only{RESET}")
        return {"skipped": True, "reason": "non_interactive"}

    do_feed = _ask("Capture your LinkedIn feed now?", default=True)
    if not do_feed:
        print("    Skipped.")
        return {"skipped": True, "reason": "user_skipped"}

    # Load JS from reader script
    rc, js_text, _ = _run([PY, str(READER_SCRIPT), "--capture-js"])
    if rc != 0 or not js_text:
        print(_err("Could not load capture JS from linkedin_session_reader.py"))
        return {"skipped": False, "ok": False, "error": "js_load_failed"}

    clipboard_ok = _try_clipboard(js_text)

    print(f"""
  {_b('Browser steps — takes about 60 seconds:')}

    1. Open  {CYAN}https://www.linkedin.com/feed{RESET}  in your browser
    2. Scroll down to load 20–40 posts
    3. Press  {_b('F12')}  →  open the  {_b('Console')}  tab
    4. {f'{GREEN}JS already copied to your clipboard.{RESET}' if clipboard_ok else f'Copy the JS:  {CYAN}python3 system/scripts/linkedin_session_reader.py --capture-js | pbcopy{RESET}'}
       Paste it into the console and press  {_b('Enter')}
    5. The console prints a JSON block — select ALL of it and copy
    6. Paste into a text editor, save as  {CYAN}{FEED_TMP_DEFAULT}{RESET}
""")

    feed_path_str = _ask_str(
        "Path to saved JSON (Enter when saved)",
        default=str(FEED_TMP_DEFAULT),
    )
    feed_path = Path(feed_path_str) if feed_path_str else FEED_TMP_DEFAULT

    if not feed_path.exists():
        # One retry
        print(f"    {_warn(f'File not found: {feed_path}')}")
        feed_path_str = _ask_str("Try again — path to saved JSON", default=str(FEED_TMP_DEFAULT))
        feed_path = Path(feed_path_str) if feed_path_str else FEED_TMP_DEFAULT
        if not feed_path.exists():
            print(f"    {_err('File still not found — skipping feed capture.')}")
            return {"skipped": False, "ok": False, "error": "file_not_found"}

    print(f"    {_arrow('Ingesting feed posts...')}", end="  ", flush=True)
    rc, stdout, stderr = _run([PY, str(READER_SCRIPT), "--ingest", "--in", str(feed_path)])
    if rc == 0:
        try:
            data = json.loads(stdout)
            ingested = data.get("ingested", "?")
            total = data.get("social_feed_posts", "?")
            print(_ok(f"{ingested} posts ingested  ({total} total in feed)"))
        except Exception:  # noqa: BLE001
            print(_ok("done"))
        return {"skipped": False, "ok": True}
    else:
        print(_err(f"rc={rc}"))
        if stderr:
            print(f"    {DIM}{stderr[:200]}{RESET}")
        return {"skipped": False, "ok": False, "error": stderr[:200]}


# ---------------------------------------------------------------------------
# Step 4: Own-post engagement (optional)
# ---------------------------------------------------------------------------

def step_engagement(*, non_interactive: bool = False) -> dict[str, Any]:
    _sep("Step 4: Own-Post Engagement (optional)")

    if non_interactive:
        return {"skipped": True, "reason": "non_interactive"}

    do_eng = _ask("Capture engagement on one of your LinkedIn posts?", default=False)
    if not do_eng:
        print("    Skipped.")
        return {"skipped": True, "reason": "user_skipped"}

    rc, js_text, _ = _run([PY, str(READER_SCRIPT), "--capture-own-post-engagement-js"])
    clipboard_ok = _try_clipboard(js_text) if (rc == 0 and js_text) else False

    print(f"""
  {_b('Browser steps:')}

    1. Open a LinkedIn post where you are the author
       (or a post's analytics page)
    2. Press  {_b('F12')}  →  open the  {_b('Console')}  tab
    3. {f'{GREEN}JS already copied to your clipboard.{RESET}' if clipboard_ok else f'Copy the JS:  {CYAN}python3 system/scripts/linkedin_session_reader.py --capture-own-post-engagement-js | pbcopy{RESET}'}
       Paste and press  {_b('Enter')}
    4. Copy the JSON output, save as  {CYAN}{ENGAGEMENT_TMP_DEFAULT}{RESET}
""")

    eng_path_str = _ask_str(
        "Path to saved engagement JSON",
        default=str(ENGAGEMENT_TMP_DEFAULT),
    )
    eng_path = Path(eng_path_str) if eng_path_str else ENGAGEMENT_TMP_DEFAULT

    if not eng_path.exists():
        print(f"    {_err(f'File not found: {eng_path} — skipping.')}")
        return {"skipped": False, "ok": False, "error": "file_not_found"}

    print(f"    {_arrow('Ingesting engagement...')}", end="  ", flush=True)
    rc, stdout, stderr = _run([
        PY, str(READER_SCRIPT),
        "--ingest-own-engagement", "--in", str(eng_path), "--confirm",
    ])
    if rc == 0:
        try:
            data = json.loads(stdout)
            written = data.get("events_written", "?")
            new_ev = data.get("new_events", "?")
            print(_ok(f"{new_ev} new events  ({written} total in cache)"))
        except Exception:  # noqa: BLE001
            print(_ok("done"))
        return {"skipped": False, "ok": True}
    else:
        print(_err(f"rc={rc}"))
        if stderr:
            print(f"    {DIM}{stderr[:200]}{RESET}")
        return {"skipped": False, "ok": False, "error": stderr[:200]}


# ---------------------------------------------------------------------------
# Step 5: Refresh social overlay caches
# ---------------------------------------------------------------------------

def step_refresh() -> dict[str, Any]:
    _sep("Refreshing Social Caches")
    print(f"    {_arrow('refresh_sources.py --social --save-health...')}", end="  ", flush=True)
    rc, stdout, stderr = _run([PY, str(REFRESH_SCRIPT), "--social", "--save-health"], timeout=120)
    if rc == 0:
        print(_ok("done"))
        return {"ok": True}
    else:
        print(_err(f"rc={rc}"))
        if stderr:
            print(f"    {DIM}{stderr[:200]}{RESET}")
        return {"ok": False, "error": stderr[:200]}


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

def cmd_status() -> int:
    print(f"\n{_b('RB LinkedIn Intelligence Status')}")
    _print_health()

    # Unprocessed ZIPs in Downloads
    dl_zips = _find_downloads_zips()
    processed = _watcher_processed_hashes()
    new_dl = [p for p in dl_zips if _file_hash(p) not in processed]
    if new_dl:
        print(f"\n  {_warn(f'{len(new_dl)} unprocessed LinkedIn ZIP(s) in ~/Downloads:')}")
        for p in new_dl:
            print(f"    {p.name}")

    # Unprocessed messages in inbox
    if INBOX_EXPORTS_DIR.exists():
        msg_done = _messages_processed_hashes() | _watcher_messages_hashes()
        new_msgs = [
            p for p in sorted(INBOX_EXPORTS_DIR.iterdir())
            if p.is_file()
            and p.suffix.lower() == ".zip"
            and _has_messages_csv(p)
            and _file_hash(p) not in msg_done
        ]
        if new_msgs:
            print(f"\n  {_warn(f'{len(new_msgs)} inbox ZIP(s) with unprocessed messages:')}")
            for p in new_msgs:
                print(f"    {p.name}")

    if _any_li_stale() or new_dl:
        print(f"\n  {YELLOW}Run:{RESET}  python3 system/scripts/li_capture.py  {DIM}(~2 min){RESET}")
    else:
        print(f"\n  {_ok('All LinkedIn sources are current.')}")
    print()
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--status", action="store_true",
                   help="Show LinkedIn source health and exit.")
    p.add_argument("--export-only", action="store_true",
                   help="Only run steps 1+2 (export ZIPs + messages ingest).")
    p.add_argument("--feed-only", action="store_true",
                   help="Only run step 3 (feed capture).")
    p.add_argument("--engagement-only", action="store_true",
                   help="Only run step 4 (own-post engagement).")
    p.add_argument("--non-interactive", action="store_true",
                   help="Auto-confirm non-browser steps; skip browser-required steps.")
    args = p.parse_args()

    if args.status:
        return cmd_status()

    non_interactive = args.non_interactive
    results: dict[str, Any] = {}

    # Header
    print(f"\n{'═' * 58}")
    print(f"  {_b('RB LinkedIn Intelligence Gatherer')}")
    print(f"  Refresh your LI signals after the daily brief.")
    print(f"{'═' * 58}")
    _print_health()

    if args.feed_only:
        results["feed"] = step_feed(non_interactive=non_interactive)
        if not results["feed"].get("skipped"):
            results["refresh"] = step_refresh()

    elif args.engagement_only:
        results["engagement"] = step_engagement(non_interactive=non_interactive)
        if not results["engagement"].get("skipped"):
            results["refresh"] = step_refresh()

    elif args.export_only:
        results["exports"] = step_export_scan(non_interactive=non_interactive)
        results["messages"] = step_messages(non_interactive=non_interactive)
        results["refresh"] = step_refresh()

    else:
        # Full interactive workflow — all 5 steps
        results["exports"] = step_export_scan(non_interactive=non_interactive)
        results["messages"] = step_messages(non_interactive=non_interactive)
        results["feed"] = step_feed(non_interactive=non_interactive)
        results["engagement"] = step_engagement(non_interactive=non_interactive)
        results["refresh"] = step_refresh()

    # ---------- Summary ----------
    _sep("Summary")
    any_work_done = False
    skipped_reasons: list[str] = []

    for key, r in results.items():
        if r.get("skipped"):
            reason = r.get("reason", "")
            # Only surface non-trivial skips
            if reason not in ("user_skipped", "non_interactive",
                              "all_already_processed", "no_downloads_found"):
                skipped_reasons.append(f"{key}: {reason}")
        else:
            any_work_done = True
            ok = r.get("ok", True)
            if ok:
                print(f"    {_ok(key)}")
            else:
                err = r.get("error", "error")
                print(f"    {_err(f'{key}: {err}')}")

    for reason in skipped_reasons:
        print(f"    {DIM}skipped — {reason}{RESET}")

    if not any_work_done:
        print(f"    {DIM}Nothing to do — LinkedIn sources are current.{RESET}")

    print()
    print(f"  {DIM}Re-run anytime: python3 system/scripts/li_capture.py{RESET}")
    print(f"  {DIM}Status check:   python3 system/scripts/li_capture.py --status{RESET}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
