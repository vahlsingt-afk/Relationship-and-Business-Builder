#!/usr/bin/env python3
"""
task_delivery_check.py - delivery-status report for the RB native ChatGPT Task path.

Checks everything on the RB/local side without needing a running server or fastapi:
  - Latest artifact exists and is dated today
  - LaunchAgent loaded and last exit status
  - OpenAPI server URL (named tunnel vs quick tunnel)
  - API server reachable (optional live check when --live flag is passed)
  - Morning pipeline cache status
  - Custom GPT instructions deployment copy committed

Prints a manual checklist for the ChatGPT-side steps that cannot be automated.

Usage:
    python3 task_delivery_check.py           # offline checks
    python3 task_delivery_check.py --live    # offline + curl /health check
    python3 task_delivery_check.py --json    # machine-readable output
    python3 task_delivery_check.py --smoke   # offline checks as smoke test (no fastapi needed)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SYSTEM_DIR.parent
CACHE_PATH = SYSTEM_DIR / ".cache" / "task_delivery_check.json"
LATEST_BRIEF_PATH = SYSTEM_DIR / "published" / "daily" / "latest_brief.json"
LATEST_HTML_PATH = SYSTEM_DIR / "published" / "daily" / "latest.html"
MORNING_PIPELINE_CACHE = SYSTEM_DIR / ".cache" / "morning_pipeline.json"
OPENAPI_GPT_PATH = SYSTEM_DIR / "api" / "openapi_gpt.yaml"
LAUNCHAGENT_LABEL = "com.relationshipbuilder.morning-pipeline"
CUSTOM_GPT_INSTRUCTIONS_PATH = SYSTEM_DIR / "api" / "custom_gpt_instructions_8k.md"
LINKEDIN_MESSAGES_PATH = SYSTEM_DIR / "inbox" / "linkedin.messages.json"
LINKEDIN_SESSION_PATH = SYSTEM_DIR / "inbox" / "linkedin.session_captures.jsonl"
SOCIAL_FEED_PATH = SYSTEM_DIR / "inbox" / "social.feed.json"
SOCIAL_OWN_POSTS_PATH = SYSTEM_DIR / "inbox" / "social.own_posts.json"
SOCIAL_ENGAGEMENT_PATH = SYSTEM_DIR / "inbox" / "social.engagement.json"
LINKEDIN_OWN_ENGAGEMENT_CONFIG_PATH = SYSTEM_DIR / ".cache" / "linkedin_own_engagement_status.json"
LINKEDIN_EXPORT_MANIFEST_PATH = SYSTEM_DIR / ".cache" / "linkedin_export_watcher.json"
LINKEDIN_MESSAGES_STALE_HOURS = 48
LINKEDIN_SESSION_STALE_HOURS = 36
LINKEDIN_OWN_POSTS_STALE_HOURS = 72
LINKEDIN_ENGAGEMENT_STALE_HOURS = 72
RUNNER_DIR = Path.home() / "Library" / "Application Support" / "Relationship Builder"
APP_LOG_OUT = RUNNER_DIR / "morning-pipeline.app.log"
APP_LOG_ERR = RUNNER_DIR / "morning-pipeline.app.error.log"

MANUAL_CHECKLIST = [
    "Named Cloudflare Tunnel is running  →  cloudflared tunnel list shows rb-api as HEALTHY.",
    "openapi_gpt.yaml servers[0].url is the named tunnel URL, not trycloudflare.com.",
    "Custom GPT Actions schema is republished in ChatGPT Builder.",
    "ChatGPT Task was created inside the Relationship Bridge GPT at 5:05 AM CT.",
    "Task-result email received from ChatGPT <noreply@tm.openai.com> with View message.",
    "Clicking View message opens the full brief inline with no command required.",
    "GPT fallback command 'Show today's RB Daily Brief.' returns the brief.",
    "Backup email, if transport configured, labels itself as backup (not primary delivery).",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _server_url() -> str | None:
    """Extract servers[0].url from openapi_gpt.yaml without requiring pyyaml."""
    if not OPENAPI_GPT_PATH.exists():
        return None
    for line in OPENAPI_GPT_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- url:") or stripped.startswith("url:"):
            parts = stripped.split("url:", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"').strip("'")
    return None


def _launchagent_status() -> dict:
    """Check launchd status for the morning-pipeline agent."""
    result = {"loaded": False, "last_exit": None, "detail": ""}
    try:
        out = subprocess.run(
            ["launchctl", "list", LAUNCHAGENT_LABEL],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            result["loaded"] = True
            for line in out.stdout.splitlines():
                if "LastExitStatus" in line:
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        result["last_exit"] = parts[1].strip().rstrip(";")
            result["detail"] = out.stdout.strip()
        else:
            result["detail"] = out.stderr.strip() or "not loaded"
    except FileNotFoundError:
        result["detail"] = "launchctl not found (non-macOS?)"
    except Exception as exc:  # noqa: BLE001
        result["detail"] = str(exc)
    return result


def _artifact_check() -> dict:
    today_str = date.today().isoformat()
    result = {"exists": False, "date_match": False, "size_kb": None, "artifact_date": None}
    if LATEST_BRIEF_PATH.exists():
        result["exists"] = True
        result["size_kb"] = round(LATEST_BRIEF_PATH.stat().st_size / 1024, 1)
        try:
            data = json.loads(LATEST_BRIEF_PATH.read_text(encoding="utf-8"))
            result["artifact_date"] = data.get("today") or data.get("date")
            result["date_match"] = (result["artifact_date"] == today_str)
        except Exception:  # noqa: BLE001
            pass
    return result


def _pipeline_cache() -> dict:
    if not MORNING_PIPELINE_CACHE.exists():
        return {"exists": False}
    try:
        data = json.loads(MORNING_PIPELINE_CACHE.read_text(encoding="utf-8"))
        # Support both field-name conventions used across RB versions.
        run_date = data.get("run_date") or data.get("date")
        ok_val = data.get("ok")
        status = data.get("status") or ("success" if ok_val is True else ("fail" if ok_val is False else None))
        return {"exists": True, "run_date": run_date, "status": status}
    except Exception:  # noqa: BLE001
        return {"exists": True, "parse_error": True}


def _pipeline_app_log_check(cache: dict) -> tuple[str, str]:
    """Detect failures hidden behind launchctl's /usr/bin/open success.

    The LaunchAgent opens a background app wrapper so macOS can grant Full Disk
    Access to that wrapper. launchctl's LastExitStatus only reflects whether
    `/usr/bin/open` launched the wrapper, not whether the Python pipeline inside
    the app succeeded. The app logs are therefore the real scheduled-run proof.
    """
    logs = [p for p in (APP_LOG_OUT, APP_LOG_ERR) if p.exists()]
    if not logs:
        return (
            "warn",
            "app wrapper logs missing — run: python3 system/scripts/morning_pipeline_install.py --run-now",
        )

    latest_log = max(logs, key=lambda p: p.stat().st_mtime)
    cache_mtime = MORNING_PIPELINE_CACHE.stat().st_mtime if MORNING_PIPELINE_CACHE.exists() else 0.0
    recent_lines: list[str] = []
    for p in logs:
        recent_lines.extend(p.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
    recent_text = "\n".join(recent_lines)
    failure_markers = (
        "[FAIL]",
        "PermissionError",
        "Operation not permitted",
        "can't open file",
        "morning pipeline: FAIL",
    )
    has_failure = any(marker in recent_text for marker in failure_markers)

    if has_failure and latest_log.stat().st_mtime >= cache_mtime:
        return (
            "fail",
            "latest app-wrapper run failed — check: python3 system/scripts/morning_pipeline_install.py --logs. "
            "Likely fix: grant Full Disk Access to the Relationship Builder Morning Pipeline app, "
            "then run: python3 system/scripts/morning_pipeline_install.py --run-now",
        )

    run_date = cache.get("run_date") or cache.get("date")
    status = cache.get("status") or ("success" if cache.get("ok") is True else None)
    if run_date == date.today().isoformat() and status == "success":
        return "pass", "latest pipeline cache is newer than any wrapper failure"

    if has_failure:
        return (
            "warn",
            "older app-wrapper failures are present in logs; latest pipeline cache was not proven by the wrapper",
        )
    return "pass", "no recent app-wrapper failure markers"


def _live_health_check(server_url: str | None) -> dict:
    """Attempt a curl /health call if --live is requested."""
    if not server_url:
        return {"status": "skip", "reason": "no server URL configured"}
    import os
    api_key = os.environ.get("RB_API_KEY", "")
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "10"]
    if api_key:
        cmd += ["-H", f"x-api-key: {api_key}"]
    cmd.append(f"{server_url.rstrip('/')}/health")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        code = out.stdout.strip()
        return {"status": "pass" if code == "200" else "fail", "http_code": code}
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "reason": str(exc)}


# ---------------------------------------------------------------------------
# LinkedIn source checks
# ---------------------------------------------------------------------------

def _linkedin_api_config_check() -> tuple[str, str]:
    """Check whether linkedin_own_engagement.py is configured (env-based, warn only)."""
    import os
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
    author_urn = os.environ.get("LINKEDIN_AUTHOR_URN", "").strip()
    org_urn = os.environ.get("LINKEDIN_ORGANIZATION_URN", "").strip()
    if not token:
        return (
            "warn",
            "LINKEDIN_ACCESS_TOKEN not set — official LinkedIn API adapter not configured. "
            "Set env vars and run: python3 system/scripts/linkedin_own_engagement.py --status. "
            "Fallback: use export watcher or browser-session capture.",
        )
    if not author_urn and not org_urn:
        return (
            "warn",
            "LINKEDIN_ACCESS_TOKEN set but no LINKEDIN_AUTHOR_URN or LINKEDIN_ORGANIZATION_URN — "
            "run: python3 system/scripts/linkedin_own_engagement.py --status",
        )
    return "pass", "LinkedIn API env vars configured (token set, URN set)"


def _linkedin_own_posts_check() -> tuple[str, str]:
    """Check freshness of social.own_posts.json."""
    if not SOCIAL_OWN_POSTS_PATH.exists():
        return (
            "warn",
            "social.own_posts.json missing — run one of: "
            "python3 system/scripts/linkedin_own_engagement.py --fetch --confirm  (official API), "
            "python3 system/scripts/linkedin_own_engagement.py --from-fixture ... --confirm  (fixture), "
            "python3 system/scripts/linkedin_export_watcher.py --ingest-new --confirm  (export), "
            "python3 system/scripts/mutations.py my-post-add  (manual). "
            "Then: python3 system/scripts/refresh_sources.py --social --save-health",
        )
    try:
        data = json.loads(SOCIAL_OWN_POSTS_PATH.read_text(encoding="utf-8"))
        fetched_at_str = data.get("fetched_at")
        post_count = len(data.get("posts") or [])
        if not fetched_at_str:
            return "warn", f"social.own_posts.json exists but has no fetched_at ({post_count} posts)"
        fetched_at = datetime.fromisoformat(fetched_at_str)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hours > LINKEDIN_OWN_POSTS_STALE_HOURS:
            return (
                "warn",
                f"social.own_posts.json stale ({age_hours:.0f}h, threshold {LINKEDIN_OWN_POSTS_STALE_HOURS}h, "
                f"{post_count} posts) — refresh: "
                "python3 system/scripts/linkedin_own_engagement.py --fetch --confirm",
            )
        return "pass", f"fresh ({age_hours:.0f}h old, {post_count} posts, source={data.get('source', '?')})"
    except Exception as exc:  # noqa: BLE001
        return "fail", f"social.own_posts.json unreadable: {exc}"


def _linkedin_own_engagement_check() -> tuple[str, str]:
    """Check freshness of social.engagement.json."""
    if not SOCIAL_ENGAGEMENT_PATH.exists():
        return (
            "warn",
            "social.engagement.json missing — run one of: "
            "python3 system/scripts/linkedin_own_engagement.py --fetch --confirm  (official API), "
            "python3 system/scripts/linkedin_session_reader.py --ingest-own-engagement --in <file> --confirm  (browser capture). "
            "Then: python3 system/scripts/refresh_sources.py --social --save-health",
        )
    try:
        data = json.loads(SOCIAL_ENGAGEMENT_PATH.read_text(encoding="utf-8"))
        fetched_at_str = data.get("fetched_at")
        event_count = len(data.get("events") or [])
        if not fetched_at_str:
            return "warn", f"social.engagement.json exists but has no fetched_at ({event_count} events)"
        fetched_at = datetime.fromisoformat(fetched_at_str)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hours > LINKEDIN_ENGAGEMENT_STALE_HOURS:
            return (
                "warn",
                f"social.engagement.json stale ({age_hours:.0f}h, threshold {LINKEDIN_ENGAGEMENT_STALE_HOURS}h, "
                f"{event_count} events) — refresh: "
                "python3 system/scripts/linkedin_own_engagement.py --fetch --confirm  or  "
                "python3 system/scripts/linkedin_session_reader.py --capture-own-post-engagement-js",
            )
        return "pass", f"fresh ({age_hours:.0f}h old, {event_count} events, source={data.get('source', '?')})"
    except Exception as exc:  # noqa: BLE001
        return "fail", f"social.engagement.json unreadable: {exc}"


def _linkedin_export_watcher_check() -> tuple[str, str]:
    """Check whether the export watcher manifest exists and report unprocessed files."""
    import sys as _sys
    scripts_dir = Path(__file__).resolve().parent
    exports_dir = SYSTEM_DIR / "inbox" / "linkedin_exports"
    messages_export = SYSTEM_DIR / "inbox" / "linkedin_messages_export.csv"

    # Count drop-location files
    drop_files: list[Path] = []
    if exports_dir.exists():
        drop_files = [f for f in exports_dir.iterdir()
                      if f.is_file() and f.suffix.lower() in (".zip", ".csv")]
    if messages_export.exists():
        drop_files.append(messages_export)

    if not drop_files:
        return (
            "warn",
            "No LinkedIn export files detected in drop locations "
            "(system/inbox/linkedin_exports/ or system/inbox/linkedin_messages_export.csv). "
            "Drop a LinkedIn Messages export or ZIP and run: "
            "python3 system/scripts/linkedin_export_watcher.py --scan",
        )

    # Check manifest for unprocessed files
    manifest_path = LINKEDIN_EXPORT_MANIFEST_PATH
    processed_hashes: set[str] = set()
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            processed_hashes = set(m.get("processed", {}).keys())
        except Exception:  # noqa: BLE001
            pass

    unprocessed = 0
    for path in drop_files:
        try:
            import hashlib
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            fhash = h.hexdigest()[:32]
            if fhash not in processed_hashes:
                unprocessed += 1
        except Exception:  # noqa: BLE001
            unprocessed += 1

    if unprocessed > 0:
        return (
            "warn",
            f"{unprocessed} unprocessed LinkedIn export file(s) in drop locations — run: "
            "python3 system/scripts/linkedin_export_watcher.py --scan  (preview), then: "
            "python3 system/scripts/linkedin_export_watcher.py --ingest-new --confirm",
        )

    return (
        "pass",
        f"{len(drop_files)} export file(s) in drop locations, all processed "
        f"(manifest: {len(processed_hashes)} entries)",
    )


def _linkedin_messaging_check() -> tuple[str, str]:
    """Return (status, reason) for the LinkedIn messages inbox cache."""
    if not LINKEDIN_MESSAGES_PATH.exists():
        return (
            "warn",
            "linkedin.messages.json missing — "
            "download Messages export -> drop at system/inbox/linkedin_messages_export.csv -> "
            "python3 system/scripts/linkedin_messaging.py --ingest ... --confirm",
        )
    try:
        data = json.loads(LINKEDIN_MESSAGES_PATH.read_text(encoding="utf-8"))
        fetched_at_str = data.get("fetched_at")
        if not fetched_at_str:
            return "warn", "linkedin.messages.json exists but has no fetched_at timestamp"
        fetched_at = datetime.fromisoformat(fetched_at_str)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        msg_count = len(data.get("messages") or [])
        if age_hours > LINKEDIN_MESSAGES_STALE_HOURS:
            return (
                "warn",
                f"stale ({age_hours:.0f}h old, threshold {LINKEDIN_MESSAGES_STALE_HOURS}h, "
                f"{msg_count} messages) — re-ingest: "
                "python3 system/scripts/linkedin_messaging.py --ingest ... --confirm",
            )
        return "pass", f"fresh ({age_hours:.0f}h old, {msg_count} messages, source={data.get('source', '?')})"
    except Exception as exc:  # noqa: BLE001
        return "warn", f"linkedin.messages.json unreadable: {exc}"


def _linkedin_session_check() -> tuple[str, str]:
    """Return (status, reason) for the LinkedIn browser-session feed."""
    # Check social.feed.json for any non-expired linkedin_browser_session posts.
    feed_posts = 0
    session_posts = 0
    latest_capture: str | None = None

    if SOCIAL_FEED_PATH.exists():
        try:
            feed = json.loads(SOCIAL_FEED_PATH.read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc)
            for post in (feed.get("posts") or []):
                if post.get("captured_via") == "linkedin_browser_session":
                    session_posts += 1
                    cat = post.get("captured_at")
                    if cat and (not latest_capture or cat > latest_capture):
                        latest_capture = cat
                feed_posts = len(feed.get("posts") or [])
        except Exception:  # noqa: BLE001
            pass

    if session_posts == 0:
        # Also check raw session file for context.
        raw_records = 0
        if LINKEDIN_SESSION_PATH.exists():
            for line in LINKEDIN_SESSION_PATH.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    raw_records += 1
        if raw_records > 0:
            return (
                "warn",
                f"{raw_records} raw session record(s) in buffer but none materialized in "
                "social.feed.json — run: python3 system/scripts/linkedin_session_reader.py --purge",
            )
        return (
            "warn",
            "no LinkedIn browser-session captures in social.feed.json — "
            "run: python3 system/scripts/linkedin_session_reader.py --capture-js, "
            "paste in browser, save output, then: "
            "python3 system/scripts/linkedin_session_reader.py --ingest --in /tmp/linkedin_posts.json",
        )

    if latest_capture:
        try:
            cap_dt = datetime.fromisoformat(latest_capture)
            if cap_dt.tzinfo is None:
                cap_dt = cap_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - cap_dt).total_seconds() / 3600
            if age_hours > LINKEDIN_SESSION_STALE_HOURS:
                return (
                    "warn",
                    f"session posts present but capture is {age_hours:.0f}h old "
                    f"(TTL {LINKEDIN_SESSION_STALE_HOURS}h) — re-capture recommended",
                )
            return "pass", f"{session_posts} session post(s) in feed, captured {age_hours:.0f}h ago"
        except Exception:  # noqa: BLE001
            pass

    return "pass", f"{session_posts} LinkedIn session post(s) in social.feed.json"


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def run_checks(live: bool = False) -> dict:
    today_str = date.today().isoformat()
    steps: dict[str, str] = {}
    reasons: dict[str, str] = {}

    # 1. Latest artifact
    art = _artifact_check()
    if not art["exists"]:
        steps["latest_brief_artifact"] = "fail"
        reasons["latest_brief_artifact"] = "latest_brief.json missing — run morning_pipeline.py"
    elif not art["date_match"]:
        steps["latest_brief_artifact"] = "warn"
        reasons["latest_brief_artifact"] = f"artifact date={art['artifact_date']} (expected {today_str})"
    else:
        steps["latest_brief_artifact"] = "pass"
        reasons["latest_brief_artifact"] = f"dated {art['artifact_date']}, {art['size_kb']} KB"

    html_exists = LATEST_HTML_PATH.exists()
    steps["latest_html_artifact"] = "pass" if html_exists else "warn"
    reasons["latest_html_artifact"] = (
        f"{round(LATEST_HTML_PATH.stat().st_size / 1024, 1)} KB"
        if html_exists else "latest.html missing"
    )

    # 2. Morning pipeline cache
    cache = _pipeline_cache()
    if not cache.get("exists"):
        steps["morning_pipeline_cache"] = "fail"
        reasons["morning_pipeline_cache"] = "no cache — pipeline may not have run yet"
    else:
        cache_date = cache.get("run_date", "")
        cache_status = cache.get("status", "unknown")
        is_today = (cache_date == today_str)
        steps["morning_pipeline_cache"] = "pass" if (is_today and cache_status == "success") else "fail"
        reasons["morning_pipeline_cache"] = f"run_date={cache_date}, status={cache_status}"

    app_status, app_reason = _pipeline_app_log_check(cache)
    steps["morning_pipeline_app_wrapper"] = app_status
    reasons["morning_pipeline_app_wrapper"] = app_reason

    # 3. LaunchAgent
    la = _launchagent_status()
    if la["loaded"]:
        exit_val = la.get("last_exit", "0")
        is_clean = (exit_val in ("0", None, ""))
        steps["launchagent_loaded"] = "pass"
        reasons["launchagent_loaded"] = f"loaded, LastExitStatus={exit_val}"
        steps["launchagent_last_exit"] = "pass" if is_clean else "warn"
        reasons["launchagent_last_exit"] = (
            "exit 0 (clean)" if is_clean
            else f"exit {exit_val} — check logs: python3 system/scripts/morning_pipeline_install.py --logs"
        )
    else:
        steps["launchagent_loaded"] = "warn"
        reasons["launchagent_loaded"] = (
            f"not loaded — run: python3 system/scripts/morning_pipeline_install.py\n"
            f"  then: python3 system/scripts/morning_pipeline_install.py --run-now\n"
            f"  detail: {la['detail']}"
        )
        steps["launchagent_last_exit"] = "skip"
        reasons["launchagent_last_exit"] = "agent not loaded"

    # 4. Tunnel URL
    url = _server_url()
    if not url:
        steps["tunnel_url"] = "fail"
        reasons["tunnel_url"] = "openapi_gpt.yaml has no servers[0].url"
    elif "trycloudflare.com" in url:
        steps["tunnel_url"] = "warn"
        reasons["tunnel_url"] = f"quick tunnel still configured: {url} — set up named tunnel"
    else:
        steps["tunnel_url"] = "pass"
        reasons["tunnel_url"] = url

    # 5. Custom GPT instructions file committed
    if CUSTOM_GPT_INSTRUCTIONS_PATH.exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(PROJECT_DIR), "ls-files", "--error-unmatch",
                 str(CUSTOM_GPT_INSTRUCTIONS_PATH)],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0:
                steps["custom_gpt_instructions_committed"] = "pass"
                reasons["custom_gpt_instructions_committed"] = "committed"
            else:
                steps["custom_gpt_instructions_committed"] = "warn"
                reasons["custom_gpt_instructions_committed"] = "exists but not committed"
        except Exception:  # noqa: BLE001
            steps["custom_gpt_instructions_committed"] = "pass"
            reasons["custom_gpt_instructions_committed"] = "file exists (git check skipped)"
    else:
        steps["custom_gpt_instructions_committed"] = "fail"
        reasons["custom_gpt_instructions_committed"] = "custom_gpt_instructions_8k.md not found"

    # 6. LinkedIn source checks (warnings only — not blockers for delivery)
    li_msg_status, li_msg_reason = _linkedin_messaging_check()
    steps["linkedin_messaging"] = li_msg_status
    reasons["linkedin_messaging"] = li_msg_reason

    li_feed_status, li_feed_reason = _linkedin_session_check()
    steps["linkedin_session_feed"] = li_feed_status
    reasons["linkedin_session_feed"] = li_feed_reason

    # 7. LinkedIn own-post engagement checks (RB 9.8, warn only)
    li_api_status, li_api_reason = _linkedin_api_config_check()
    steps["linkedin_api_config"] = li_api_status
    reasons["linkedin_api_config"] = li_api_reason

    li_own_posts_status, li_own_posts_reason = _linkedin_own_posts_check()
    steps["linkedin_own_posts"] = li_own_posts_status
    reasons["linkedin_own_posts"] = li_own_posts_reason

    li_eng_status, li_eng_reason = _linkedin_own_engagement_check()
    steps["linkedin_own_engagement"] = li_eng_status
    reasons["linkedin_own_engagement"] = li_eng_reason

    li_watcher_status, li_watcher_reason = _linkedin_export_watcher_check()
    steps["linkedin_export_watcher"] = li_watcher_status
    reasons["linkedin_export_watcher"] = li_watcher_reason

    # 7. Optional live /health check
    if live:
        health = _live_health_check(url)
        steps["live_health_api"] = health.get("status", "fail")
        reasons["live_health_api"] = (
            f"HTTP {health.get('http_code', '?')}" if "http_code" in health
            else health.get("reason", "unknown")
        )

    pass_count = sum(1 for v in steps.values() if v == "pass")
    warn_count = sum(1 for v in steps.values() if v == "warn")
    fail_count = sum(1 for v in steps.values() if v == "fail")

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": today_str,
        "server_url": url,
        "automated_steps": steps,
        "reasons": reasons,
        "automated_pass_count": pass_count,
        "automated_warn_count": warn_count,
        "automated_fail_count": fail_count,
        "manual_checklist_printed": True,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _print_result(result: dict) -> None:
    icons = {"pass": "✓", "warn": "⚠", "fail": "✗", "skip": "–"}
    print(f"\nRB Delivery Status — {result['today']}  (checked {result['checked_at']})")
    print(f"  Server URL: {result.get('server_url') or '(not set)'}")
    print()
    print("Automated checks:")
    for name, status in (result.get("automated_steps") or {}).items():
        icon = icons.get(status, "?")
        reason = (result.get("reasons") or {}).get(name, "")
        print(f"  {icon} {name}: {reason}")
    p = result.get("automated_pass_count", 0)
    w = result.get("automated_warn_count", 0)
    f = result.get("automated_fail_count", 0)
    print(f"\n  Summary: {p} pass, {w} warn, {f} fail")
    print()
    print("Manual verification (mark each done before updating STATUS.md):")
    for item in MANUAL_CHECKLIST:
        print(f"  [ ] {item}")
    print()


def _smoke() -> int:
    """Smoke test that does not require fastapi or a running server."""
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK  " if cond else "FAIL"
        print(f"  {mark}  {msg}")
        if not cond:
            failures.append(msg)

    result = run_checks(live=False)
    steps = result["automated_steps"]

    ck("latest_brief_artifact" in steps, "latest_brief_artifact check recorded")
    ck("latest_html_artifact" in steps, "latest_html_artifact check recorded")
    ck("morning_pipeline_cache" in steps, "morning_pipeline_cache check recorded")
    ck("launchagent_loaded" in steps, "launchagent_loaded check recorded")
    ck("tunnel_url" in steps, "tunnel_url check recorded")
    ck("custom_gpt_instructions_committed" in steps, "custom_gpt_instructions check recorded")
    ck("linkedin_messaging" in steps, "linkedin_messaging check recorded")
    ck("linkedin_session_feed" in steps, "linkedin_session_feed check recorded")
    # LinkedIn checks must never be fail (warn or pass only) — missing sources are warnings
    li_msg = steps.get("linkedin_messaging", "")
    li_feed = steps.get("linkedin_session_feed", "")
    ck(li_msg in ("pass", "warn"), f"linkedin_messaging is warn or pass (got {li_msg!r})")
    ck(li_feed in ("pass", "warn"), f"linkedin_session_feed is warn or pass (got {li_feed!r})")
    # Recovery reasons are present for warn states
    if li_msg == "warn":
        ck(bool(result.get("reasons", {}).get("linkedin_messaging")),
           "linkedin_messaging warn includes recovery reason")
    if li_feed == "warn":
        ck(bool(result.get("reasons", {}).get("linkedin_session_feed")),
           "linkedin_session_feed warn includes recovery reason")
    # RB 9.8 new LinkedIn checks
    ck("linkedin_api_config" in steps, "linkedin_api_config check recorded")
    ck("linkedin_own_posts" in steps, "linkedin_own_posts check recorded")
    ck("linkedin_own_engagement" in steps, "linkedin_own_engagement check recorded")
    ck("linkedin_export_watcher" in steps, "linkedin_export_watcher check recorded")
    li_api = steps.get("linkedin_api_config", "")
    li_own = steps.get("linkedin_own_posts", "")
    li_eng = steps.get("linkedin_own_engagement", "")
    li_watch = steps.get("linkedin_export_watcher", "")
    ck(li_api in ("pass", "warn"), f"linkedin_api_config is warn or pass (got {li_api!r})")
    ck(li_own in ("pass", "warn", "fail"), f"linkedin_own_posts is valid status (got {li_own!r})")
    ck(li_eng in ("pass", "warn", "fail"), f"linkedin_own_engagement is valid status (got {li_eng!r})")
    ck(li_watch in ("pass", "warn"), f"linkedin_export_watcher is warn or pass (got {li_watch!r})")
    for key in ("linkedin_api_config", "linkedin_own_posts", "linkedin_own_engagement", "linkedin_export_watcher"):
        if steps.get(key) == "warn":
            ck(bool(result.get("reasons", {}).get(key)), f"{key} warn includes recovery reason")
    ck(result["manual_checklist_printed"] is True, "manual checklist flag recorded")
    ck(CACHE_PATH.exists(), "task_delivery_check cache written")
    ck(len(MANUAL_CHECKLIST) >= 7, "manual checklist has platform verification steps")

    print(f"\n--- task_delivery_check smoke: {len(failures)} failure(s) ---")
    return 1 if failures else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="Emit JSON output.")
    p.add_argument("--live", action="store_true", help="Run live /health curl check against server URL.")
    p.add_argument("--smoke", action="store_true", help="Run offline smoke test (no fastapi needed).")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    result = run_checks(live=args.live)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_result(result)
    return 0 if result.get("automated_fail_count", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
