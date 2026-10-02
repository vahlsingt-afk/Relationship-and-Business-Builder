#!/usr/bin/env python3
"""
tunnel_watchdog.py - self-healing check for the shared cloudflared tunnel.

One named tunnel ("rb-api") serves three public hostnames from a single
launchd-managed cloudflared process (see ~/.cloudflared/config.yaml):
  - rb-api.bridgepointops.org     -> 127.0.0.1:8765 (Custom GPT Actions)
  - rbb-chat.bridgepointops.org   -> 127.0.0.1:8766 (RBB Chat)
  - rbb-lookup.bridgepointops.org -> 127.0.0.1:8767 (Team Portal)

If any hostname is unreachable with a Cloudflare edge error (530, 502,
521-526, ...), the whole tunnel process is assumed down or wedged (this is
exactly what happened on 2026-09-15: cloudflared's self-updater replaced the
running binary, the launchd job's next spawn failed with EX_CONFIG, and
launchd gave up retrying until manually kicked). This script resets the
shared tunnel launchd job and rechecks.

rbb-lookup has a second, independent failure mode: unlike the other two, its
origin (com.relationshipbuilder.team-portal) has no other supervisor tying
it to the tunnel, so the tunnel can be healthy while the origin itself is
simply not running (a 502 with the tunnel otherwise alive -- this is what
happened on 2026-09-28, undetected until a manual check, because this script
did not check rbb-lookup at all). If rbb-lookup is still down after a tunnel
reset while the other two hostnames are fine, this script additionally
resets the team-portal launchd job specifically.

Meant to run on an interval via com.relationshipbuilder.cloudflared-watchdog.plist,
not interactively. Reuses tunnel_health_check.py's rb-api probe logic rather
than re-implementing it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SYSTEM_DIR.parent
CACHE_PATH = SYSTEM_DIR / ".cache" / "tunnel_watchdog.json"
LAUNCHD_LABEL = "com.relationshipbuilder.cloudflared-tunnel"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
TEAM_PORTAL_LABEL = "com.relationshipbuilder.team-portal"
TEAM_PORTAL_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{TEAM_PORTAL_LABEL}.plist"
RBB_CHAT_URL = "https://rbb-chat.bridgepointops.org"
RBB_LOOKUP_URL = "https://rbb-lookup.bridgepointops.org"
RESET_SETTLE_SECONDS = 8

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tunnel_health_check as rb_api_check  # noqa: E402


def _check_url(url: str, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": "RelationshipBuilderTunnelWatchdog/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"alive": 200 <= response.status < 300, "http_status": response.status, "error": None}
    except urllib.error.HTTPError as exc:
        # Cloudflare edge error codes (530, 502, 521-526, ...) mean the tunnel
        # itself is down, not that the app answered - only 2xx counts as alive.
        return {"alive": False, "http_status": exc.code, "error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"alive": False, "http_status": None, "error": f"{type(exc).__name__}: {exc}"}


def _check_rbb_chat(timeout: float = 8.0) -> dict:
    return _check_url(RBB_CHAT_URL, timeout=timeout)


def _check_rbb_lookup(timeout: float = 8.0) -> dict:
    return _check_url(RBB_LOOKUP_URL, timeout=timeout)


def _reset_launchd_job(label: str = LAUNCHD_LABEL, plist: Path = LAUNCHD_PLIST) -> dict:
    uid_result = subprocess.run(["id", "-u"], capture_output=True, text=True, check=True)
    uid = uid_result.stdout.strip()
    domain = f"gui/{uid}/{label}"
    bootout = subprocess.run(["launchctl", "bootout", domain], capture_output=True, text=True)
    time.sleep(2)
    bootstrap = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True, text=True
    )
    return {
        "bootout_rc": bootout.returncode,
        "bootout_stderr": bootout.stderr.strip(),
        "bootstrap_rc": bootstrap.returncode,
        "bootstrap_stderr": bootstrap.stderr.strip(),
    }


def run() -> dict:
    result: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reset_triggered": False,
    }

    rb_api = rb_api_check.check()
    rbb_chat = _check_rbb_chat()
    rbb_lookup = _check_rbb_lookup()
    result["rb_api"] = {"alive": rb_api["tunnel_alive"], "http_status": rb_api["http_status"], "error": rb_api["error"]}
    result["rbb_chat"] = rbb_chat
    result["rbb_lookup"] = rbb_lookup

    if rb_api["tunnel_alive"] and rbb_chat["alive"] and rbb_lookup["alive"]:
        result["status"] = "alive"
        return result

    result["reset_triggered"] = True
    result["reset"] = _reset_launchd_job()
    time.sleep(RESET_SETTLE_SECONDS)

    rb_api_after = rb_api_check.check()
    rbb_chat_after = _check_rbb_chat()
    rbb_lookup_after = _check_rbb_lookup()
    result["rb_api_after_reset"] = {
        "alive": rb_api_after["tunnel_alive"], "http_status": rb_api_after["http_status"], "error": rb_api_after["error"]
    }
    result["rbb_chat_after_reset"] = rbb_chat_after
    result["rbb_lookup_after_reset"] = rbb_lookup_after

    # rbb-lookup can stay down even with a perfectly healthy tunnel: its
    # origin (team-portal) isn't tied to the tunnel process at all, so a
    # tunnel reset does nothing for it. Only take this second swing when the
    # other two came back clean -- otherwise the tunnel itself is still the
    # problem and hammering team-portal too would just add noise.
    if not rbb_lookup_after["alive"] and rb_api_after["tunnel_alive"] and rbb_chat_after["alive"]:
        result["team_portal_reset_triggered"] = True
        result["team_portal_reset"] = _reset_launchd_job(TEAM_PORTAL_LABEL, TEAM_PORTAL_PLIST)
        time.sleep(RESET_SETTLE_SECONDS)
        rbb_lookup_after = _check_rbb_lookup()
        result["rbb_lookup_after_reset"] = rbb_lookup_after

    result["status"] = (
        "recovered"
        if (rb_api_after["tunnel_alive"] and rbb_chat_after["alive"] and rbb_lookup_after["alive"])
        else "still_down"
    )
    return result


def main() -> int:
    result = run()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    ts = result["checked_at"]
    if result["status"] == "alive":
        print(f"{ts} alive (no action)")
        return 0
    if result["status"] == "recovered":
        print(f"{ts} was down - reset launchd job - recovered")
        return 0
    print(f"{ts} was down - reset launchd job - STILL DOWN: {json.dumps(result)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
