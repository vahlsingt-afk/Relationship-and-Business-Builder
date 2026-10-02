#!/usr/bin/env python3
"""
morning_path_test.py - end-to-end proof for the RB morning delivery path.

Automated steps:
  0. tunnel_health_check.py (non-blocking visibility for Custom GPT Action URL)
  1. refresh_sources.py --all --save-health
  2. daily_brief.py --smoke
  3. publish.py --write --confirm
  4. GET /daily_brief?use_cache=true through FastAPI TestClient
  5. GET /brief/health when the endpoint is available

Manual checks printed at the end cover native ChatGPT Task delivery and the GPT
fallback command. The script writes system/.cache/morning_path_test.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
PROJECT_DIR = SYSTEM_DIR.parent
CACHE_PATH = SYSTEM_DIR / ".cache" / "morning_path_test.json"
SOURCE_HEALTH_PATH = SYSTEM_DIR / ".cache" / "source_health.json"
PUBLISHED_DIR = SYSTEM_DIR / "published" / "daily"
TUNNEL_HEALTH_PATH = SYSTEM_DIR / ".cache" / "tunnel_health.json"


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _step(name: str, status: str, reason: str, *, automated: bool = True) -> dict:
    return {
        "name": name,
        "status": status,
        "reason": reason,
        "automated": automated,
    }


def _print_step(step: dict) -> None:
    print(f"[{step['status']}] {step['name']}: {step['reason']}")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _tunnel_health(py: str) -> dict:
    rc, stdout, stderr = _run([py, str(SCRIPTS_DIR / "tunnel_health_check.py"), "--timeout", "5"])
    health = _load_json(TUNNEL_HEALTH_PATH)
    alive = bool(health.get("tunnel_alive"))
    url = health.get("server_url") or "(missing)"
    error = health.get("error") or stderr.strip() or stdout.strip()
    status = "PASS" if alive else "WARN"
    reason = f"tunnel_alive={str(alive).lower()}; url={url}"
    if not alive and error:
        reason += f"; error={error}"
    step = _step("tunnel health", status, reason)
    step["tunnel_alive"] = alive
    step["server_url"] = health.get("server_url")
    step["health_url"] = health.get("health_url")
    step["http_status"] = health.get("http_status")
    step["cache_path"] = str(TUNNEL_HEALTH_PATH.relative_to(PROJECT_DIR))
    step["returncode"] = rc
    return step


def _source_refresh(py: str) -> dict:
    rc, stdout, stderr = _run([py, str(SCRIPTS_DIR / "refresh_sources.py"), "--all", "--save-health"])
    if rc != 0:
        msg = stderr.strip().splitlines()[-1] if stderr.strip() else stdout.strip().splitlines()[-1:]
        return _step(
            "source refresh",
            "FAIL",
            f"Source refresh failed: {msg or 'refresh_sources.py returned non-zero'}. "
            "Brief may be under-instrumented. Run: `python3 system/scripts/refresh_sources.py --all --save-health` and check the reason.",
        )
    if not SOURCE_HEALTH_PATH.exists():
        return _step(
            "source refresh",
            "FAIL",
            "Source refresh failed: source_health.json was not written. "
            "Run: `python3 system/scripts/refresh_sources.py --all --save-health` and check the reason.",
        )
    health = _load_json(SOURCE_HEALTH_PATH)
    if "overall_health" not in health:
        return _step(
            "source refresh",
            "FAIL",
            "Source refresh failed: source_health.json is missing overall_health.",
        )
    failed = [
        f"{name} reported {row.get('status')}"
        for name, row in (health.get("sources") or {}).items()
        if isinstance(row, dict) and row.get("status") == "failed"
    ]
    if failed:
        return _step(
            "source refresh",
            "FAIL",
            "Source refresh failed: "
            + "; ".join(failed[:3])
            + ". Brief may be under-instrumented. Run: `python3 system/scripts/refresh_sources.py --all --save-health` and check the reason.",
        )
    return _step(
        "source refresh",
        "PASS",
        f"source_health.json written; overall_health={health.get('overall_health')}.",
    )


def _daily_brief_smoke(py: str) -> dict:
    rc, stdout, stderr = _run([py, str(SCRIPTS_DIR / "daily_brief.py"), "--smoke"])
    if rc != 0:
        detail = stderr.strip() or stdout.strip().splitlines()[-1] if stdout.strip() else "unknown failure"
        return _step(
            "daily brief smoke",
            "FAIL",
            f"Daily brief smoke failed: {detail}. Do not publish until resolved.",
        )
    return _step("daily brief smoke", "PASS", "daily_brief.py --smoke reported 0 failures.")


def _publish_artifacts(py: str, today: date) -> dict:
    rc, stdout, stderr = _run([
        py,
        str(SCRIPTS_DIR / "publish.py"),
        "--date",
        today.isoformat(),
        "--write",
        "--confirm",
    ])
    if rc != 0:
        detail = stderr.strip() or stdout.strip() or "publish.py returned non-zero"
        return _step(
            "publish artifacts",
            "FAIL",
            f"Published artifacts missing at {PUBLISHED_DIR / today.isoformat()}. Run `publish.py` and check for errors. {detail}",
        )
    expected = [
        PUBLISHED_DIR / today.isoformat() / "index.md",
        PUBLISHED_DIR / today.isoformat() / "index.html",
        PUBLISHED_DIR / today.isoformat() / "brief.json",
        PUBLISHED_DIR / "latest.html",
    ]
    missing = [str(p.relative_to(PROJECT_DIR)) for p in expected if not p.exists()]
    if missing:
        return _step(
            "publish artifacts",
            "FAIL",
            f"Published artifacts missing at {', '.join(missing)}. Run `publish.py` and check for errors.",
        )
    return _step(
        "publish artifacts",
        "PASS",
        f"published artifacts exist at system/published/daily/{today.isoformat()} and latest.html.",
    )


def _api_checks() -> list[dict]:
    sys.path.insert(0, str(SYSTEM_DIR / "scripts"))
    sys.path.insert(0, str(SYSTEM_DIR / "api"))
    try:
        from fastapi.testclient import TestClient
        import server  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return [
            _step(
                "api daily brief",
                "FAIL",
                f"GET /daily_brief returned unavailable TestClient setup: {exc}. Is FastAPI installed?",
            ),
            _step(
                "brief health",
                "MANUAL",
                "GET /brief/health not checked because API TestClient setup failed.",
                automated=False,
            ),
        ]

    client = TestClient(server.app)
    headers = {"x-api-key": os.environ["RB_API_KEY"]} if os.environ.get("RB_API_KEY") else {}
    steps: list[dict] = []
    r = client.get("/daily_brief?use_cache=true", headers=headers)
    if r.status_code == 200 and "canonical_brief" in r.json():
        steps.append(_step("api daily brief", "PASS", "GET /daily_brief?use_cache=true returned 200 with canonical_brief."))
    else:
        steps.append(_step(
            "api daily brief",
            "FAIL",
            f"GET /daily_brief returned {r.status_code} or missing canonical_brief key. Is the server running? Is the Cloudflare tunnel active?",
        ))

    r = client.get("/brief/health", headers=headers)
    if r.status_code == 200 and "overall_health" in r.json():
        steps.append(_step("brief health", "PASS", "GET /brief/health returned 200 with overall_health."))
    elif r.status_code == 404:
        steps.append(_step(
            "brief health",
            "MANUAL",
            "GET /brief/health is not implemented yet; Priority 4 owns this endpoint.",
            automated=False,
        ))
    else:
        steps.append(_step(
            "brief health",
            "FAIL",
            f"GET /brief/health returned {r.status_code}. Source health endpoint not reachable.",
        ))
    return steps


def main() -> int:
    py = sys.executable or "python3"
    today = date.today()
    steps = [
        _tunnel_health(py),
        _source_refresh(py),
        _daily_brief_smoke(py),
        _publish_artifacts(py, today),
        *_api_checks(),
        _step(
            "ChatGPT Task verification",
            "MANUAL",
            '"View message" email received; brief visible without sending a command.',
            automated=False,
        ),
        _step(
            "GPT fallback command verification",
            "MANUAL",
            '"Show today\'s RB Daily Brief." returns the brief.',
            automated=False,
        ),
    ]

    for step in steps:
        _print_step(step)

    automated = [s for s in steps if s["automated"]]
    blocking = [s for s in automated if s["status"] != "WARN"]
    passed = sum(1 for s in blocking if s["status"] == "PASS")
    warned = sum(1 for s in automated if s["status"] == "WARN")
    print(f"Morning path: {passed}/{len(blocking)} blocking automated steps passed; {warned} warning(s)")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "steps": steps,
        "tunnel_alive": next((s.get("tunnel_alive") for s in steps if s["name"] == "tunnel health"), None),
        "automated_passed": passed,
        "automated_total": len(blocking),
        "automated_warnings": warned,
        "ok": passed == len(blocking),
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
