#!/usr/bin/env python3
"""strava_sync.py — pull recent Strava activities into RB inbox.

Refreshes OAuth2 token, fetches last 14 days of activities, and writes
to system/inbox/strava_activities.json. Called from morning_pipeline.py.

Reads credentials from life_goals.yaml (strava section) or env vars:
  RB_STRAVA_CLIENT_ID, RB_STRAVA_CLIENT_SECRET, RB_STRAVA_REFRESH_TOKEN

Usage:
    python3 strava_sync.py              # sync and write to inbox
    python3 strava_sync.py --dry-run    # print activities, don't write
    python3 strava_sync.py --auth       # first-time auth helper (get tokens)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip3 install pyyaml")
    sys.exit(1)

GOALS_PATH = core.SYSTEM_DIR / "life_goals.yaml"
STRAVA_CACHE_PATH = core.SYSTEM_DIR / ".cache" / "strava_activities.json"
STRAVA_TOKEN_PATH = core.SYSTEM_DIR / ".cache" / "strava_token.json"

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"

LOOKBACK_DAYS = 14


def _load_credentials() -> dict | None:
    """Load Strava credentials from life_goals.yaml or env vars."""
    # Env vars take priority
    client_id = os.environ.get("RB_STRAVA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("RB_STRAVA_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("RB_STRAVA_REFRESH_TOKEN", "").strip()

    if not client_id and GOALS_PATH.exists():
        try:
            config = yaml.safe_load(GOALS_PATH.read_text(encoding="utf-8")) or {}
            strava = config.get("strava") or {}
            client_id = str(strava.get("client_id", "")).strip()
            client_secret = str(strava.get("client_secret", "")).strip()
            refresh_token = str(strava.get("refresh_token", "")).strip()
        except Exception:
            pass

    if not all([client_id, client_secret, refresh_token]):
        return None
    return {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}


def _refresh_access_token(creds: dict) -> str | None:
    """Exchange refresh token for a new access token."""
    # Check cached token first
    if STRAVA_TOKEN_PATH.exists():
        try:
            cached = json.loads(STRAVA_TOKEN_PATH.read_text())
            expires_at = cached.get("expires_at", 0)
            if expires_at > datetime.now(tz=timezone.utc).timestamp() + 300:
                return cached["access_token"]
        except Exception:
            pass

    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()

    try:
        req = urllib.request.Request(STRAVA_TOKEN_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_data = json.loads(resp.read())
    except Exception as e:
        print(f"ERROR: Token refresh failed — {e}", file=sys.stderr)
        return None

    # Cache it
    STRAVA_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    STRAVA_TOKEN_PATH.write_text(json.dumps(token_data, indent=2))
    return token_data.get("access_token")


def _fetch_activities(access_token: str, days: int = LOOKBACK_DAYS) -> list[dict]:
    """Fetch activities from the last N days."""
    since = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    url = f"{STRAVA_ACTIVITIES_URL}?after={since}&per_page=50"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"ERROR: Activity fetch failed — {e}", file=sys.stderr)
        return []


def _normalize_activity(activity: dict) -> dict:
    """Normalise a Strava activity into RB's expected format."""
    start = activity.get("start_date_local") or activity.get("start_date") or ""
    act_date = start[:10] if start else date.today().isoformat()
    distance_m = float(activity.get("distance") or 0)
    distance_mi = round(distance_m / 1609.34, 2)
    elapsed_s = int(activity.get("elapsed_time") or activity.get("moving_time") or 0)
    elapsed_min = round(elapsed_s / 60, 0)
    act_type = activity.get("type") or activity.get("sport_type") or "Workout"
    avg_hr = activity.get("average_heartrate")
    name = activity.get("name") or act_type

    return {
        "id": str(activity.get("id", "")),
        "date": act_date,
        "type": act_type,
        "name": name,
        "distance_miles": distance_mi,
        "duration_minutes": int(elapsed_min),
        "average_heartrate": avg_hr,
        "kudos": activity.get("kudos_count", 0),
        "_source": "strava",
        "_synced_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }


def run_auth_helper(creds: dict) -> None:
    """Walk the user through first-time OAuth to get a refresh token."""
    scope = "activity:read_all"
    params = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "response_type": "code",
        "redirect_uri": "http://localhost",
        "approval_prompt": "force",
        "scope": scope,
    })
    auth_url = f"{STRAVA_AUTH_URL}?{params}"
    print("\nStep 1: Open this URL in your browser and authorise RB:")
    print(f"\n  {auth_url}\n")
    print("Step 2: After authorising, you'll be redirected to localhost.")
    print("         Copy the 'code' parameter from the URL bar.\n")
    code = input("Paste the code here: ").strip()

    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
    }).encode()

    try:
        req = urllib.request.Request(STRAVA_TOKEN_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_data = json.loads(resp.read())
    except Exception as e:
        print(f"ERROR: Token exchange failed — {e}")
        return

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh token in response.")
        print(json.dumps(token_data, indent=2))
        return

    print(f"\n✓ Authorised! Your refresh token is:\n\n  {refresh_token}\n")
    print("Add this to life_goals.yaml under strava.refresh_token")
    print("or set env var: export RB_STRAVA_REFRESH_TOKEN=<token>")

    # Auto-update life_goals.yaml if it exists
    if GOALS_PATH.exists():
        update = input("\nUpdate life_goals.yaml automatically? [Y/n]: ").strip().lower()
        if not update or update.startswith("y"):
            config = yaml.safe_load(GOALS_PATH.read_text(encoding="utf-8")) or {}
            if "strava" not in config:
                config["strava"] = {}
            config["strava"]["refresh_token"] = refresh_token
            config["strava"]["enabled"] = True
            GOALS_PATH.write_text(
                yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)
            )
            print("✓ life_goals.yaml updated.")

    # Cache the token
    STRAVA_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    STRAVA_TOKEN_PATH.write_text(json.dumps(token_data, indent=2))


def sync(dry_run: bool = False, verbose: bool = False) -> dict:
    creds = _load_credentials()
    if not creds:
        msg = "Strava credentials not configured. Run: python3 strava_sync.py --auth"
        print(f"✗ {msg}")
        return {"ok": False, "error": msg}

    access_token = _refresh_access_token(creds)
    if not access_token:
        return {"ok": False, "error": "Token refresh failed"}

    activities = _fetch_activities(access_token)
    normalized = [_normalize_activity(a) for a in activities]

    if verbose or dry_run:
        print(f"Fetched {len(normalized)} activities (last {LOOKBACK_DAYS} days):")
        for a in normalized:
            dist = f" {a['distance_miles']}mi" if a["distance_miles"] > 0 else ""
            hr = f" @{a['average_heartrate']:.0f}bpm" if a["average_heartrate"] else ""
            print(f"  {a['date']} {a['type']}: {a['name']}{dist} {a['duration_minutes']}min{hr}")

    if not dry_run:
        STRAVA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STRAVA_CACHE_PATH.write_text(
            json.dumps({
                "synced_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                "activities": normalized,
            }, indent=2, default=str),
            encoding="utf-8"
        )

    print(f"✓ Strava: {len(normalized)} activities synced")
    return {"ok": True, "count": len(normalized), "activities": normalized}


def main() -> int:
    p = argparse.ArgumentParser(description="RB Strava sync")
    p.add_argument("--dry-run", action="store_true", help="Print activities, don't write")
    p.add_argument("--auth", action="store_true", help="First-time OAuth setup")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    if args.auth:
        creds = _load_credentials()
        if not creds or not creds.get("client_id"):
            print("Set client_id and client_secret in life_goals.yaml first.")
            return 1
        run_auth_helper(creds)
        return 0

    result = sync(dry_run=args.dry_run, verbose=args.verbose)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
