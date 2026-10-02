#!/usr/bin/env python3
"""
tunnel_health_check.py - verify the Custom GPT Action tunnel endpoint.

Reads servers[0].url from system/api/openapi_gpt.yaml, calls <url>/health,
and writes system/.cache/tunnel_health.json. This check is deliberately about
the public tunnel, not the local FastAPI/TestClient backend.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SYSTEM_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SYSTEM_DIR.parent
OPENAPI_GPT_PATH = SYSTEM_DIR / "api" / "openapi_gpt.yaml"
SETTINGS_PATH = SYSTEM_DIR / "settings.json"
CACHE_PATH = SYSTEM_DIR / ".cache" / "tunnel_health.json"


def _server_url() -> str | None:
    if not OPENAPI_GPT_PATH.exists():
        return None
    for line in OPENAPI_GPT_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- url:") or stripped.startswith("url:"):
            return stripped.split("url:", 1)[1].strip().strip('"').strip("'")
    return None


def _find_key(obj: Any) -> str | None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.lower() in {"rb_api_key", "api_key", "x_api_key"} and isinstance(value, str) and value:
                return value
            found = _find_key(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key(item)
            if found:
                return found
    return None


def _api_key() -> tuple[str | None, str]:
    env_key = os.environ.get("RB_API_KEY")
    if env_key:
        return env_key, "env:RB_API_KEY"
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None, "none"
    key = _find_key(settings)
    return (key, "settings.json") if key else (None, "none")


def _restart_commands() -> list[str]:
    cloudflared = ".tools/cloudflared" if (PROJECT_DIR / ".tools" / "cloudflared").exists() else "cloudflared"
    return [
        "cd " + str(PROJECT_DIR),
        "RB_API_KEY=${RB_API_KEY:-localtest} python3 -m uvicorn system.api.server:app --host 127.0.0.1 --port 8765",
        f"{cloudflared} tunnel --url http://127.0.0.1:8765",
        "python3 system/scripts/update_tunnel_url.py https://<new-trycloudflare-or-named-tunnel-url>",
        "python3 system/scripts/validate_openapi_gpt.py",
        "Republish the Custom GPT Action schema in ChatGPT Builder.",
    ]


def check(timeout: float = 8.0) -> dict:
    url = _server_url()
    key, key_source = _api_key()
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server_url": url,
        "health_url": f"{url.rstrip('/')}/health" if url else None,
        "api_key_source": key_source,
        "tunnel_alive": False,
        "status": "dead",
        "http_status": None,
        "error": None,
        "restart_commands": [],
    }
    if not url:
        result["error"] = "openapi_gpt.yaml has no servers[0].url"
        result["restart_commands"] = _restart_commands()
        return result

    request = urllib.request.Request(
        result["health_url"],
        headers={"User-Agent": "RelationshipBuilderTunnelHealth/1.0"},
    )
    if key:
        request.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator URL
            body = response.read(4096).decode("utf-8", errors="replace")
            result["http_status"] = response.status
            result["response_preview"] = body[:400]
            if 200 <= response.status < 300:
                result["tunnel_alive"] = True
                result["status"] = "alive"
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["error"] = f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"

    if not result["tunnel_alive"]:
        result["restart_commands"] = _restart_commands()
    return result


def _print(result: dict) -> None:
    mark = "alive" if result["tunnel_alive"] else "dead"
    print(f"Tunnel: {mark}")
    print(f"URL:    {result.get('server_url') or '(missing)'}")
    if result.get("http_status") is not None:
        print(f"HTTP:   {result['http_status']}")
    if result.get("error"):
        print(f"Error:  {result['error']}")
    print(f"API key source: {result.get('api_key_source')}")
    if not result["tunnel_alive"]:
        print()
        print("Restart/update commands:")
        for command in result["restart_commands"]:
            print(f"  {command}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout seconds.")
    args = parser.parse_args()
    result = check(timeout=args.timeout)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print(result)
    return 0 if result["tunnel_alive"] else 1


if __name__ == "__main__":
    sys.exit(main())
