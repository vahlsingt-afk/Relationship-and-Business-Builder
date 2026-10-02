#!/usr/bin/env python3
"""
durable_tunnel_install.py — install/check persistent RB API + named tunnel jobs.

This script does not create a Cloudflare account or browser login. First run:

  .tools/cloudflared tunnel login
  .tools/cloudflared tunnel create rb-api
  .tools/cloudflared tunnel route dns rb-api rb-api.<your-zone>.com

Then create ~/.cloudflared/config.yaml pointing rb-api.<your-zone>.com to
http://127.0.0.1:8765. After that this script can install LaunchAgents that
keep both the local FastAPI server and named tunnel running after reboot.
"""
from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SYSTEM_DIR.parent
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = SYSTEM_DIR / "automation"
RUNNER_DIR = Path.home() / "Library" / "Application Support" / "Relationship Builder"

API_LABEL = "com.relationshipbuilder.api-server"
TUNNEL_LABEL = "com.relationshipbuilder.cloudflared-tunnel"
API_PLIST = LAUNCH_AGENTS / f"{API_LABEL}.plist"
TUNNEL_PLIST = LAUNCH_AGENTS / f"{TUNNEL_LABEL}.plist"
API_RUNNER = RUNNER_DIR / "api_server_runner.zsh"
API_APP_PATH = RUNNER_DIR / "Relationship Builder API Server.app"
API_APP_EXEC = API_APP_PATH / "Contents" / "MacOS" / "rb-api-server"
API_APP_INFO = API_APP_PATH / "Contents" / "Info.plist"
API_APP_LOG_OUT = RUNNER_DIR / "api-server.app.log"
API_APP_LOG_ERR = RUNNER_DIR / "api-server.app.error.log"
VENDOR_SRC = SYSTEM_DIR / "vendor" / "py39"
VENDOR_RUNTIME = RUNNER_DIR / "vendor" / "py39"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20)


def _python() -> str:
    return str(Path(sys.executable or "/usr/bin/python3").resolve())


def _cloudflared() -> str:
    bundled = PROJECT_DIR / ".tools" / "cloudflared"
    if bundled.exists():
        return str(bundled)
    return "cloudflared"


def _launchctl(label: str) -> tuple[bool, str]:
    if platform.system() != "Darwin":
        return False, "not macOS"
    rc = _run(["launchctl", "list", label])
    return rc.returncode == 0, (rc.stdout or rc.stderr).strip()


def _render_api_runner(api_key: str) -> str:
    py = shlex.quote(_python())
    project = shlex.quote(str(PROJECT_DIR))
    vendor = shlex.quote(str(VENDOR_RUNTIME))
    key = shlex.quote(api_key)
    return (
        "#!/bin/zsh\n"
        "set -euo pipefail\n"
        f"export RB_API_KEY={key}\n"
        f"export RB_PROJECT_DIR={project}\n"
        "export PYTHONNOUSERSITE=1\n"
        f"export PYTHONPATH={vendor}\n"
        f"cd {project}\n"
        "exec "
        f"{py} -m uvicorn system.api.server:app --host 127.0.0.1 --port 8765 --loop asyncio --http h11\n"
    )


def _render_api_app_executable() -> str:
    runner = shlex.quote(str(API_RUNNER))
    out_log = shlex.quote(str(API_APP_LOG_OUT))
    err_log = shlex.quote(str(API_APP_LOG_ERR))
    return "#!/bin/zsh\n" f"exec {runner} >> {out_log} 2>> {err_log}\n"


def _render_api_app_info() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>rb-api-server</string>
    <key>CFBundleIdentifier</key>
    <string>com.relationshipbuilder.apiserver</string>
    <key>CFBundleName</key>
    <string>Relationship Builder API Server</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSBackgroundOnly</key>
    <true/>
</dict>
</plist>
"""


def _write_api_app(api_key: str) -> None:
    RUNNER_DIR.mkdir(parents=True, exist_ok=True)
    if VENDOR_SRC.exists():
        VENDOR_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
        if VENDOR_RUNTIME.exists():
            shutil.rmtree(VENDOR_RUNTIME)
        shutil.copytree(VENDOR_SRC, VENDOR_RUNTIME, copy_function=shutil.copy)
    (API_APP_PATH / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    API_RUNNER.write_text(_render_api_runner(api_key), encoding="utf-8")
    API_RUNNER.chmod(0o755)
    API_APP_EXEC.write_text(_render_api_app_executable(), encoding="utf-8")
    API_APP_EXEC.chmod(0o755)
    API_APP_INFO.write_text(_render_api_app_info(), encoding="utf-8")


def _fda_warning() -> str:
    py = _python()
    return (
        "IMPORTANT: If the API server exits with 'Operation not permitted', grant Full Disk Access to\n"
        "the Python binary used to run the server in System Settings → Privacy & Security → Full Disk Access:\n\n"
        f"    {py}\n\n"
        "After granting FDA, restart the LaunchAgent:\n"
        "  launchctl unload ~/Library/LaunchAgents/com.relationshipbuilder.api-server.plist\n"
        "  launchctl load -w ~/Library/LaunchAgents/com.relationshipbuilder.api-server.plist\n\n"
        "Or reinstall cleanly:\n"
        "  RB_API_KEY=<key> python3 system/scripts/durable_tunnel_install.py install --api-key <key>"
    )


def _check_http(url: str, api_key: str = "", timeout: float = 4.0) -> tuple[bool, str]:
    """Return (ok, detail) for an HTTP health probe."""
    try:
        headers = {"User-Agent": "RelationshipBuilderStatus/1.0"}
        if api_key:
            headers["x-api-key"] = api_key
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} {exc.reason}"
    except OSError as exc:
        return False, str(exc)[:80]


def _plist_api(api_key: str) -> dict:
    # Launch Python directly so macOS Full Disk Access has one stable target.
    # This avoids both the old /usr/bin/open → app-bundle indirection and a
    # shell parent process whose child may be evaluated separately by TCC.
    return {
        "Label": API_LABEL,
        "ProgramArguments": [
            _python(),
            "-m",
            "uvicorn",
            "system.api.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--loop",
            "asyncio",
            "--http",
            "h11",
        ],
        "WorkingDirectory": str(PROJECT_DIR),
        "EnvironmentVariables": {
            "RB_API_KEY": api_key,
            "RB_PROJECT_DIR": str(PROJECT_DIR),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(VENDOR_RUNTIME),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG_DIR / "api-server.log"),
        "StandardErrorPath": str(LOG_DIR / "api-server.error.log"),
    }


def _plist_tunnel(tunnel_name: str) -> dict:
    return {
        "Label": TUNNEL_LABEL,
        "ProgramArguments": [
            _cloudflared(),
            "tunnel",
            "run",
            "--protocol",
            "http2",
            tunnel_name,
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG_DIR / "cloudflared-tunnel.log"),
        "StandardErrorPath": str(LOG_DIR / "cloudflared-tunnel.error.log"),
    }


def _write_plist(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(payload, f, sort_keys=False)


def status() -> int:
    api_key = os.environ.get("RB_API_KEY", "")
    ok = "✓"
    fail = "✗"

    print("=" * 52)
    print("Durable Tunnel Status")
    print("=" * 52)

    # --- File/config checks ---
    cf = _cloudflared()
    cf_config = (
        (Path.home() / ".cloudflared" / "config.yaml").exists()
        or (Path.home() / ".cloudflared" / "config.yml").exists()
    )
    print(f"\n[Files]")
    print(f"  cloudflared binary : {cf}")
    print(f"  cloudflared config : {ok if cf_config else fail}")
    print(f"  API plist          : {ok if API_PLIST.exists() else fail}  ({API_PLIST})")
    print(f"  tunnel plist       : {ok if TUNNEL_PLIST.exists() else fail}")
    print(f"  API runner script  : {ok if API_RUNNER.exists() else fail}  ({API_RUNNER})")

    # --- LaunchAgent load status ---
    print(f"\n[LaunchAgents]")
    for label in (API_LABEL, TUNNEL_LABEL):
        loaded, detail = _launchctl(label)
        print(f"  {label}")
        print(f"    loaded : {ok if loaded else fail}")
        if detail:
            for line in detail.splitlines()[:6]:
                print(f"    {line}")

    # --- HTTP health probes ---
    print(f"\n[HTTP health]")

    local_ok, local_detail = _check_http("http://127.0.0.1:8765/health", api_key)
    print(f"  local  http://127.0.0.1:8765/health  : {ok if local_ok else fail}  {local_detail}")

    public_url = "https://rb-api.bridgepointops.org/health"
    pub_ok, pub_detail = _check_http(public_url, api_key)
    print(f"  public {public_url}  : {ok if pub_ok else fail}  {pub_detail}")

    # --- API auth check ---
    print(f"\n[API auth]")
    if not api_key:
        print("  (set RB_API_KEY env var to test authenticated endpoints)")
    else:
        auth_ok, auth_detail = _check_http("http://127.0.0.1:8765/brief/health", api_key)
        print(f"  GET /brief/health with x-api-key : {ok if auth_ok else fail}  {auth_detail}")

    # --- Overall verdict ---
    all_ok = local_ok and pub_ok and API_PLIST.exists() and API_RUNNER.exists()
    print(f"\n{'[OK] All checks passed.' if all_ok else '[DEGRADED] One or more checks failed — see above.'}")
    if not local_ok:
        print("  → API server is not running locally. Check LaunchAgent and FDA.")
        print(f"  → Logs: {LOG_DIR / 'api-server.error.log'}")
    if not pub_ok:
        print("  → Public tunnel is unreachable. Check cloudflared LaunchAgent.")
        print(f"  → Logs: {LOG_DIR / 'cloudflared-tunnel.error.log'}")

    return 0 if all_ok else 1


def install(api_key: str, tunnel_name: str) -> int:
    if platform.system() != "Darwin":
        print("ERROR: LaunchAgent install is macOS-only.", file=sys.stderr)
        return 2
    if not api_key:
        print("ERROR: provide --api-key or set RB_API_KEY.", file=sys.stderr)
        return 2
    _write_api_app(api_key)
    _write_plist(API_PLIST, _plist_api(api_key))
    _write_plist(TUNNEL_PLIST, _plist_tunnel(tunnel_name))
    for plist in (API_PLIST, TUNNEL_PLIST):
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        rc = subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, text=True)
        if rc.returncode != 0:
            print(f"launchctl load failed for {plist}: {rc.stderr.strip()}", file=sys.stderr)
            return 1
    print(f"Installed and loaded {API_LABEL} and {TUNNEL_LABEL}.")
    print("Verify:")
    print("  RB_API_KEY=<key> python3 system/scripts/tunnel_health_check.py")
    print("  RB_API_KEY=<key> python3 system/scripts/task_delivery_check.py --live")
    print()
    print(_fda_warning())
    return 0


def uninstall() -> int:
    for plist in (API_PLIST, TUNNEL_PLIST):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink()
            print(f"Removed {plist}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_install = sub.add_parser("install")
    p_install.add_argument("--api-key", default=os.environ.get("RB_API_KEY"))
    p_install.add_argument("--tunnel-name", default="rb-api")
    sub.add_parser("uninstall")
    args = parser.parse_args()
    if args.cmd == "status":
        return status()
    if args.cmd == "install":
        return install(args.api_key or "", args.tunnel_name)
    return uninstall()


if __name__ == "__main__":
    raise SystemExit(main())
