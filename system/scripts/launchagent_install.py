#!/usr/bin/env python3
"""
launchagent_install.py — install (or uninstall) the macOS LaunchAgent that
fetches Apple Messages + Call History on a schedule.

This makes the phone/SMS integration *automatic* on the user's Mac — no
manual `python3 fetch_...` invocations needed once installed. The
LaunchAgent runs three times daily and on every login.

Usage:
    python3 system/scripts/launchagent_install.py            # install + start
    python3 system/scripts/launchagent_install.py --status   # check if running
    python3 system/scripts/launchagent_install.py --logs     # tail the logs
    python3 system/scripts/launchagent_install.py --run-now  # trigger one fetch immediately
    python3 system/scripts/launchagent_install.py --uninstall

macOS only. Idempotent — re-running install replaces the existing plist.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

LABEL = "com.relationshipbuilder.interaction-fetch"
SCRIPTS_DIR = core.SYSTEM_DIR / "scripts"
AUTOMATION_DIR = core.SYSTEM_DIR / "automation"
TEMPLATE_PATH = AUTOMATION_DIR / "com.relationshipbuilder.interaction-fetch.plist.template"
WRAPPER_PATH = SCRIPTS_DIR / "interaction_fetch_wrapper.py"
USER_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
INSTALLED_PLIST = USER_LAUNCH_AGENTS / f"{LABEL}.plist"
LOG_OUT = AUTOMATION_DIR / "interaction-fetch.log"
LOG_ERR = AUTOMATION_DIR / "interaction-fetch.error.log"


def _check_mac() -> None:
    if platform.system() != "Darwin":
        sys.stderr.write(
            "ERROR: This installer is macOS-only. For other platforms, see "
            "system/automation/README.md for the productized roadmap.\n"
        )
        sys.exit(2)


def _python_for_launchd() -> str:
    """Return the absolute python3 path that the LaunchAgent should invoke.
    Prefer the current interpreter; fall back to /usr/bin/python3.
    """
    candidate = sys.executable or "/usr/bin/python3"
    return str(Path(candidate).resolve())


def _render_plist() -> str:
    text = TEMPLATE_PATH.read_text()
    return (
        text
        .replace("__PYTHON__", _python_for_launchd())
        .replace("__WRAPPER__", str(WRAPPER_PATH))
        .replace("__PROJECT_DIR__", str(core.PROJECT_DIR))
        .replace("__LOG_OUT__", str(LOG_OUT))
        .replace("__LOG_ERR__", str(LOG_ERR))
    )


def _fda_warning() -> str:
    py = _python_for_launchd()
    return (
        "WARNING: The Python at " + py + " needs Full Disk Access (FDA) to\n"
        "         read ~/Library/Messages/chat.db and the CallHistory store.\n"
        "         The shell's FDA grant does NOT propagate to launchd.\n\n"
        "         To grant FDA to this Python binary:\n"
        "           1. System Settings → Privacy & Security → Full Disk Access.\n"
        "           2. Click the + button (authenticate if asked).\n"
        "           3. Press Cmd+Shift+G in the file picker.\n"
        "           4. Paste: " + py + "\n"
        "           5. Click 'Open' and ensure the toggle is on.\n"
        "           6. Re-run this installer's --run-now to verify.\n"
    )


def cmd_install() -> int:
    _check_mac()
    if not TEMPLATE_PATH.exists() or not WRAPPER_PATH.exists():
        sys.stderr.write(
            f"ERROR: missing template or wrapper.\n"
            f"  template: {TEMPLATE_PATH}\n"
            f"  wrapper:  {WRAPPER_PATH}\n"
        )
        return 2
    USER_LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    AUTOMATION_DIR.mkdir(parents=True, exist_ok=True)

    plist_body = _render_plist()
    INSTALLED_PLIST.write_text(plist_body)
    print(f"Wrote {INSTALLED_PLIST}.")

    # Unload any prior instance (ignore failure on first install)
    subprocess.run(["launchctl", "unload", str(INSTALLED_PLIST)],
                   capture_output=True)
    rc = subprocess.run(
        ["launchctl", "load", "-w", str(INSTALLED_PLIST)],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        sys.stderr.write(f"launchctl load failed: {rc.stderr.strip()}\n")
        return 1
    print(f"LaunchAgent loaded as {LABEL}.")
    print(f"Logs will appear at:")
    print(f"  {LOG_OUT}")
    print(f"  {LOG_ERR}")
    print()
    print(_fda_warning())
    print("Next steps:")
    print("  1. Grant FDA to python3 (see above).")
    print("  2. Run: python3 system/scripts/launchagent_install.py --run-now")
    print("  3. Confirm with: python3 system/scripts/launchagent_install.py --status")
    return 0


def cmd_uninstall() -> int:
    _check_mac()
    if not INSTALLED_PLIST.exists():
        print(f"No LaunchAgent installed at {INSTALLED_PLIST}.")
        return 0
    rc = subprocess.run(
        ["launchctl", "unload", str(INSTALLED_PLIST)],
        capture_output=True, text=True,
    )
    if rc.returncode != 0 and rc.stderr.strip():
        sys.stderr.write(f"launchctl unload note: {rc.stderr.strip()}\n")
    INSTALLED_PLIST.unlink()
    print(f"Removed {INSTALLED_PLIST}.")
    return 0


def cmd_status() -> int:
    _check_mac()
    print(f"Plist installed: {INSTALLED_PLIST.exists()} ({INSTALLED_PLIST})")
    rc = subprocess.run(["launchctl", "list", LABEL],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        print(f"launchctl: {LABEL} is not loaded.")
        return 1
    print("launchctl list output:")
    print(rc.stdout)
    print(f"Log file (out): {LOG_OUT}  ({'exists' if LOG_OUT.exists() else 'missing'})")
    print(f"Log file (err): {LOG_ERR}  ({'exists' if LOG_ERR.exists() else 'missing'})")
    print(f"\nLatest fetch outputs:")
    for p, label in [(core.MESSAGES_PATH, "messages.json"),
                     (core.CALLS_PATH, "calls.json")]:
        if p.exists():
            from datetime import datetime
            mtime = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
            print(f"  {label:<16} mtime={mtime}  size={p.stat().st_size:,}b")
        else:
            print(f"  {label:<16} missing (fetch has not produced output yet)")
    return 0


def cmd_run_now() -> int:
    _check_mac()
    if not INSTALLED_PLIST.exists():
        sys.stderr.write(
            "ERROR: LaunchAgent not installed. Run --install first.\n"
        )
        return 2
    rc = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        sys.stderr.write(f"launchctl kickstart failed: {rc.stderr.strip()}\n")
        return 1
    print(f"Triggered {LABEL}. Watch logs with --logs.")
    return 0


def cmd_logs() -> int:
    _check_mac()
    for p, label in [(LOG_OUT, "stdout"), (LOG_ERR, "stderr")]:
        print(f"\n===== {label} ({p}) =====")
        if not p.exists():
            print("  (file does not exist yet)")
            continue
        try:
            # Tail last ~50 lines
            with open(p) as f:
                lines = f.readlines()[-50:]
            for ln in lines:
                print(ln.rstrip())
        except Exception as e:
            sys.stderr.write(f"  ERROR reading {p}: {e}\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--install", action="store_true", default=True,
                     help="(default) Install + load the LaunchAgent.")
    grp.add_argument("--uninstall", action="store_true")
    grp.add_argument("--status", action="store_true")
    grp.add_argument("--logs", action="store_true")
    grp.add_argument("--run-now", action="store_true")
    args = p.parse_args()

    if args.uninstall:
        return cmd_uninstall()
    if args.status:
        return cmd_status()
    if args.logs:
        return cmd_logs()
    if args.run_now:
        return cmd_run_now()
    return cmd_install()


if __name__ == "__main__":
    sys.exit(main())
