#!/usr/bin/env python3
"""
morning_pipeline_install.py - install the RB morning pipeline LaunchAgents.

Two-phase scheduling (RB 9.44):

  4:00 AM  com.relationshipbuilder.pre-brief-scan  (--scan-only)
           Fetches Gmail/Calendar, refreshes all intelligence caches, runs
           intelligence_assessment.  Warms the cache so the brief has an hour
           of settled data to read from.

  5:00 AM  com.relationshipbuilder.morning-pipeline  (--brief-only)
           Builds and publishes the canonical brief from warm cache.
           Falls back to a full scan if the 4 AM job didn't run.

This ensures the 5:05 AM native ChatGPT Task notification delivers a brief
built from intelligence gathered at 4 AM, not 5 AM.
"""
from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shlex
import subprocess
import sys
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

LABEL = "com.relationshipbuilder.morning-pipeline"
LABEL_SCAN = "com.relationshipbuilder.pre-brief-scan"
SCRIPTS_DIR = core.SYSTEM_DIR / "scripts"
AUTOMATION_DIR = core.SYSTEM_DIR / "automation"
TEMPLATE_PATH = AUTOMATION_DIR / "com.relationshipbuilder.morning-pipeline.plist.template"
TEMPLATE_SCAN_PATH = AUTOMATION_DIR / "com.relationshipbuilder.pre-brief-scan.plist.template"
WRAPPER_PATH = SCRIPTS_DIR / "morning_pipeline.py"
RUNNER_DIR = Path.home() / "Library" / "Application Support" / "Relationship Builder"
RUNNER_PATH = RUNNER_DIR / "morning_pipeline_runner.zsh"
WRAPPER_COPY_PATH = RUNNER_DIR / "morning_pipeline.py"
APP_PATH = RUNNER_DIR / "Relationship Builder Morning Pipeline.app"
APP_EXEC_PATH = APP_PATH / "Contents" / "MacOS" / "rb-morning-pipeline"
APP_INFO_PATH = APP_PATH / "Contents" / "Info.plist"
SECRETS_RUNNER_PATH = RUNNER_DIR / "run_with_secrets.py"
APP_LOG_OUT = RUNNER_DIR / "morning-pipeline.app.log"
APP_LOG_ERR = RUNNER_DIR / "morning-pipeline.app.error.log"
USER_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
INSTALLED_PLIST = USER_LAUNCH_AGENTS / f"{LABEL}.plist"
INSTALLED_SCAN_PLIST = USER_LAUNCH_AGENTS / f"{LABEL_SCAN}.plist"
LOG_OUT = AUTOMATION_DIR / "morning-pipeline.log"
LOG_ERR = AUTOMATION_DIR / "morning-pipeline.error.log"
LOG_SCAN_OUT = AUTOMATION_DIR / "pre-brief-scan.log"
LOG_SCAN_ERR = AUTOMATION_DIR / "pre-brief-scan.error.log"


def _check_mac() -> None:
    if platform.system() != "Darwin":
        sys.stderr.write("ERROR: this installer is macOS-only.\n")
        sys.exit(2)


def _python_for_launchd() -> str:
    return str(Path(sys.executable or "/usr/bin/python3").resolve())


def _fda_warning() -> str:
    return (
        "IMPORTANT: Grant Full Disk Access to the Python binary used by launchd:\n"
        "        " + _python_for_launchd() + "\n\n"
        "      The LaunchAgent runs Python directly so macOS has one stable FDA target.\n"
        "      After granting access,\n"
        "      re-run: python3 system/scripts/morning_pipeline_install.py --run-now\n"
    )


def _render_runner() -> str:
    py = shlex.quote(_python_for_launchd())
    wrapper = shlex.quote(str(WRAPPER_COPY_PATH))
    project = shlex.quote(str(core.PROJECT_DIR))
    return (
        "#!/bin/zsh\n"
        "set -euo pipefail\n"
        f"export RB_PROJECT_DIR={project}\n"
        f"cd {project}\n"
        f"exec {py} {wrapper}\n"
    )


def _render_app_executable() -> str:
    runner = shlex.quote(str(RUNNER_PATH))
    out_log = shlex.quote(str(APP_LOG_OUT))
    err_log = shlex.quote(str(APP_LOG_ERR))
    return (
        "#!/bin/zsh\n"
        f"exec {runner} >> {out_log} 2>> {err_log}\n"
    )


def _render_app_info() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>rb-morning-pipeline</string>
    <key>CFBundleIdentifier</key>
    <string>com.relationshipbuilder.morningpipeline</string>
    <key>CFBundleName</key>
    <string>Relationship Builder Morning Pipeline</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSBackgroundOnly</key>
    <true/>
</dict>
</plist>
"""


def _write_app_wrapper() -> None:
    (APP_PATH / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    APP_EXEC_PATH.write_text(_render_app_executable(), encoding="utf-8")
    APP_EXEC_PATH.chmod(0o755)
    APP_INFO_PATH.write_text(_render_app_info(), encoding="utf-8")


def _render_plist() -> str:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        text
        .replace("__PYTHON__", xml_escape(_python_for_launchd()))
        .replace("__SECRETS_RUNNER__", xml_escape(str(SECRETS_RUNNER_PATH)))
        .replace("__WRAPPER__", xml_escape(str(WRAPPER_PATH)))
        .replace("__RUNNER__", str(RUNNER_PATH))
        .replace("__APP__", str(APP_PATH))
        .replace("__PROJECT_DIR__", xml_escape(str(core.PROJECT_DIR)))
        .replace("__LOG_OUT__", xml_escape(str(LOG_OUT)))
        .replace("__LOG_ERR__", xml_escape(str(LOG_ERR)))
    )


def _read_installed_env_var(plist_path: Path, key: str) -> str:
    """Best-effort read of an EnvironmentVariables value from an already-installed plist."""
    try:
        with plist_path.open("rb") as f:
            data = plistlib.load(f)
        return str(data.get("EnvironmentVariables", {}).get(key, ""))
    except Exception:
        return ""


def _render_scan_plist() -> str:
    text = TEMPLATE_SCAN_PATH.read_text(encoding="utf-8")
    # Secrets are loaded by run_with_secrets.py. Found 2026-07-20:
    # pre-brief-scan.plist ran without the API key, so capture_process_all's LLM transcript
    # summary (transcript_summarizer.py) silently no-op'd every single day and
    # every automated capture fell back to much-weaker keyword-trigger triage
    # -- morning-pipeline.plist had the key the whole time, pre-brief-scan.plist
    # (where captures are actually processed) did not. Prefer the current
    # environment; fall back to whatever's already in the sibling
    # morning-pipeline.plist, since that's been the de facto source of truth
    # for this key.
    return (
        text
        .replace("__PYTHON__", xml_escape(_python_for_launchd()))
        .replace("__SECRETS_RUNNER__", xml_escape(str(SECRETS_RUNNER_PATH)))
        .replace("__WRAPPER__", xml_escape(str(WRAPPER_PATH)))
        .replace("__PROJECT_DIR__", xml_escape(str(core.PROJECT_DIR)))
        .replace("__LOG_OUT__", xml_escape(str(LOG_SCAN_OUT)))
        .replace("__LOG_ERR__", xml_escape(str(LOG_SCAN_ERR)))
    )


def cmd_install() -> int:
    _check_mac()
    if not TEMPLATE_PATH.exists() or not WRAPPER_PATH.exists():
        sys.stderr.write(
            f"ERROR: missing template or wrapper.\n  template: {TEMPLATE_PATH}\n  wrapper: {WRAPPER_PATH}\n"
        )
        return 2
    USER_LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    AUTOMATION_DIR.mkdir(parents=True, exist_ok=True)
    RUNNER_DIR.mkdir(parents=True, exist_ok=True)
    WRAPPER_COPY_PATH.write_text(WRAPPER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {WRAPPER_COPY_PATH}.")
    RUNNER_PATH.write_text(_render_runner(), encoding="utf-8")
    RUNNER_PATH.chmod(0o755)
    print(f"Wrote {RUNNER_PATH}.")
    _write_app_wrapper()
    print(f"Wrote {APP_PATH}.")

    # ── 4 AM pre-brief scan agent ─────────────────────────────────────────
    if TEMPLATE_SCAN_PATH.exists():
        INSTALLED_SCAN_PLIST.write_text(_render_scan_plist(), encoding="utf-8")
        print(f"Wrote {INSTALLED_SCAN_PLIST}.")
        subprocess.run(["launchctl", "unload", str(INSTALLED_SCAN_PLIST)], capture_output=True)
        rc_scan = subprocess.run(
            ["launchctl", "load", "-w", str(INSTALLED_SCAN_PLIST)],
            capture_output=True, text=True,
        )
        if rc_scan.returncode != 0:
            sys.stderr.write(f"launchctl load (pre-brief-scan) failed: {rc_scan.stderr.strip()}\n")
        else:
            print(f"LaunchAgent loaded as {LABEL_SCAN} (4:00 AM scan).")
            print(f"Scan logs: {LOG_SCAN_OUT} / {LOG_SCAN_ERR}")
    else:
        print(f"Note: pre-brief-scan template not found at {TEMPLATE_SCAN_PATH}. Skipping.")

    # ── 5 AM brief-only agent ─────────────────────────────────────────────
    INSTALLED_PLIST.write_text(_render_plist(), encoding="utf-8")
    print(f"Wrote {INSTALLED_PLIST}.")
    subprocess.run(["launchctl", "unload", str(INSTALLED_PLIST)], capture_output=True)
    rc = subprocess.run(["launchctl", "load", "-w", str(INSTALLED_PLIST)], capture_output=True, text=True)
    if rc.returncode != 0:
        sys.stderr.write(f"launchctl load failed: {rc.stderr.strip()}\n")
        return 1
    print(f"LaunchAgent loaded as {LABEL} (5:00 AM brief build).")
    print(f"Brief logs: {LOG_OUT} / {LOG_ERR}")
    print()
    print("Two-phase schedule installed:")
    print("  4:00 AM — pre-brief-scan (gather phase, warms cache)")
    print("  5:00 AM — morning-pipeline (brief build from warm cache)")
    print()
    print(_fda_warning())
    print("Run now: python3 system/scripts/morning_pipeline_install.py --run-now")
    return 0


def cmd_uninstall() -> int:
    _check_mac()
    removed = 0
    for plist, label in [
        (INSTALLED_SCAN_PLIST, LABEL_SCAN),
        (INSTALLED_PLIST, LABEL),
    ]:
        if not plist.exists():
            print(f"No LaunchAgent installed at {plist}.")
            continue
        rc = subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, text=True)
        if rc.returncode != 0 and rc.stderr.strip():
            sys.stderr.write(f"launchctl unload note ({label}): {rc.stderr.strip()}\n")
        plist.unlink()
        print(f"Removed {plist}.")
        removed += 1
    return 0 if removed > 0 else 1


def cmd_run_now() -> int:
    _check_mac()
    if INSTALLED_PLIST.exists():
        rc = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True,
            text=True,
        )
        if rc.returncode == 0:
            print(f"Triggered {LABEL}. Watch logs with --logs.")
            return 0
        sys.stderr.write(f"launchctl kickstart failed: {rc.stderr.strip()}\n")
    print("Running morning_pipeline.py directly.")
    return subprocess.call([_python_for_launchd(), str(WRAPPER_PATH)])


def cmd_status() -> int:
    _check_mac()
    print("=== Two-phase pipeline schedule ===")
    for label, plist in [(LABEL_SCAN, INSTALLED_SCAN_PLIST), (LABEL, INSTALLED_PLIST)]:
        installed = plist.exists()
        print(f"\n{label}")
        print(f"  Plist: {'installed' if installed else 'NOT installed'} ({plist})")
        rc = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
        if rc.returncode == 0:
            for line in rc.stdout.strip().splitlines():
                print(f"  {line}")
        else:
            print(f"  launchctl: not loaded")
    print()
    rc = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
    for p, label in [
        (core.SYSTEM_DIR / ".cache" / "morning_pipeline.json", "pipeline cache"),
        (core.SYSTEM_DIR / "published" / "daily" / "latest_brief.json", "latest brief json"),
        (core.SYSTEM_DIR / "published" / "daily" / "latest.html", "latest html"),
        (WRAPPER_COPY_PATH, "pipeline copy"),
        (RUNNER_PATH, "runner"),
        (APP_PATH, "app wrapper"),
        (APP_LOG_OUT, "app stdout log"),
        (APP_LOG_ERR, "app stderr log"),
        (LOG_OUT, "stdout log"),
        (LOG_ERR, "stderr log"),
    ]:
        state = "exists" if p.exists() else "missing"
        size = f", {p.stat().st_size:,}b" if p.exists() else ""
        print(f"{label}: {state}{size} ({p})")
    return 0 if rc.returncode == 0 else 1


def cmd_logs() -> int:
    _check_mac()
    for p, label in [(LOG_OUT, "stdout"), (LOG_ERR, "stderr")]:
        print(f"\n===== {label} ({p}) =====")
        if not p.exists():
            print("  (file does not exist yet)")
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        for line in lines:
            print(line)
    for p, label in [(APP_LOG_OUT, "app stdout"), (APP_LOG_ERR, "app stderr")]:
        print(f"\n===== {label} ({p}) =====")
        if not p.exists():
            print("  (file does not exist yet)")
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        for line in lines:
            print(line)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--install", action="store_true", default=True)
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
