#!/usr/bin/env python3
"""
linkedin_own_engagement.py — Official LinkedIn own-post engagement adapter.

Pulls Todd's own posts and per-post engagement through the LinkedIn official
APIs where access/scopes are available, normalizes into the existing canonical
caches, and fails safely with a structured status when credentials are absent.

Configuration (env vars — never store tokens in git):
    LINKEDIN_ACCESS_TOKEN       OAuth2 bearer token
    LINKEDIN_AUTHOR_URN         e.g. urn:li:person:<id>   (member mode)
    LINKEDIN_ORGANIZATION_URN   e.g. urn:li:organization:<id>  (org mode)
    LINKEDIN_API_VERSION        YYYYMM version string, default 202505

CLI:
    python3 linkedin_own_engagement.py --status
    python3 linkedin_own_engagement.py --fetch --dry-run
    python3 linkedin_own_engagement.py --fetch --confirm
    python3 linkedin_own_engagement.py --from-fixture system/fixtures/linkedin_own_engagement_sample.json --dry-run
    python3 linkedin_own_engagement.py --smoke

Output caches:
    system/inbox/social.own_posts.json
    system/inbox/social.engagement.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OWN_POSTS_PATH = core.SOCIAL_OWN_POSTS_PATH
ENGAGEMENT_PATH = core.SOCIAL_ENGAGEMENT_PATH
FIXTURES_DIR = core.SYSTEM_DIR / "fixtures"

DEFAULT_API_VERSION = "202505"
LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
LINKEDIN_REST_BASE = "https://api.linkedin.com/rest"

SOURCE_OFFICIAL = "linkedin_official_api"
SOURCE_FIXTURE = "linkedin_fixture"


# ---------------------------------------------------------------------------
# Configuration / status
# ---------------------------------------------------------------------------

def _env(key: str) -> str | None:
    return os.environ.get(key, "").strip() or None


def config_status() -> dict[str, Any]:
    """Return a status dict without exposing token contents."""
    token = _env("LINKEDIN_ACCESS_TOKEN")
    author_urn = _env("LINKEDIN_AUTHOR_URN")
    org_urn = _env("LINKEDIN_ORGANIZATION_URN")
    api_version = _env("LINKEDIN_API_VERSION") or DEFAULT_API_VERSION

    has_token = bool(token)
    mode = None
    if has_token and author_urn:
        mode = "member"
    elif has_token and org_urn:
        mode = "organization"
    elif has_token:
        mode = "token_only_no_urn"

    return {
        "has_token": has_token,
        "token_hint": (token[:4] + "…") if has_token else None,
        "author_urn": author_urn,
        "organization_urn": org_urn,
        "api_version": api_version,
        "mode": mode,
        "configured": mode in ("member", "organization"),
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _linkedin_get(path: str, token: str, version: str) -> dict[str, Any]:
    """GET from LinkedIn REST API. Returns parsed JSON or raises."""
    if path.startswith("http"):
        url = path
    else:
        url = f"{LINKEDIN_REST_BASE}/{path.lstrip('/')}"
    req = Request(url, headers={
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": version,
        "X-Restli-Protocol-Version": "2.0.0",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# API fetch functions
# ---------------------------------------------------------------------------

def fetch_author_posts(
    token: str,
    author_urn: str,
    api_version: str,
    count: int = 20,
) -> list[dict[str, Any]]:
    """Fetch posts by the member author. Returns raw API elements."""
    encoded = author_urn.replace(":", "%3A")
    path = f"posts?author={encoded}&q=author&count={count}&sortBy=LAST_MODIFIED"
    data = _linkedin_get(path, token, api_version)
    return data.get("elements") or []


def fetch_organization_posts(
    token: str,
    org_urn: str,
    api_version: str,
    count: int = 20,
) -> list[dict[str, Any]]:
    """Fetch posts by an organization author."""
    encoded = org_urn.replace(":", "%3A")
    path = f"posts?author={encoded}&q=author&count={count}&sortBy=LAST_MODIFIED"
    data = _linkedin_get(path, token, api_version)
    return data.get("elements") or []


def fetch_social_metadata(
    post_urns: list[str],
    token: str,
    api_version: str,
) -> dict[str, dict[str, Any]]:
    """Fetch aggregate social counts for a batch of post URNs.
    Returns mapping of urn -> metadata dict."""
    results: dict[str, dict[str, Any]] = {}
    for urn in post_urns:
        try:
            encoded = urn.replace(":", "%3A")
            path = f"socialMetadata/{encoded}"
            data = _linkedin_get(path, token, api_version)
            results[urn] = data
        except Exception:  # noqa: BLE001
            results[urn] = {}
    return results


def fetch_reactions(
    post_urn: str,
    token: str,
    api_version: str,
    count: int = 50,
) -> list[dict[str, Any]]:
    """Fetch reactions (likes etc.) for a single post."""
    encoded = post_urn.replace(":", "%3A")
    path = f"reactions?entity={encoded}&q=entity&count={count}"
    try:
        data = _linkedin_get(path, token, api_version)
        return data.get("elements") or []
    except Exception:  # noqa: BLE001
        return []


def fetch_comments(
    post_urn: str,
    token: str,
    api_version: str,
    count: int = 50,
) -> list[dict[str, Any]]:
    """Fetch comments for a single post."""
    encoded = post_urn.replace(":", "%3A")
    path = f"comments?object={encoded}&q=object&count={count}"
    try:
        data = _linkedin_get(path, token, api_version)
        return data.get("elements") or []
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# P-036 RI assessment helper
# ---------------------------------------------------------------------------

def _normalize_li_url(url: str | None) -> str:
    """Normalize a LinkedIn profile URL for comparison."""
    if not url:
        return ""
    return url.lower().rstrip("/").split("?")[0]


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(name.lower().split())


def _match_engager(engager: dict, baseline: list[dict]) -> dict | None:
    """Return the first baseline entry that matches this engager, or None."""
    li_url = _normalize_li_url(engager.get("linkedin_url") or engager.get("urn"))
    name_norm = _normalize_name(engager.get("name"))
    for entry in baseline:
        if li_url and _normalize_li_url(entry.get("linkedin_url")) == li_url:
            return entry
        if name_norm and name_norm == _normalize_name(entry.get("name")):
            return entry
    return None


def _build_engager_ri_assessment(
    engager: dict,
    *,
    baseline: list[dict] | None = None,
    source_freshness: str = "fresh",
    event_type: str = "engagement",
) -> dict:
    """Build a P-036-compliant ri_assessment block for a LinkedIn engagement event.

    Status:
      proposed   — engager matched to a baseline contact
      blocked    — engager has name/URL but no baseline match
      irrelevant — engager info too sparse to assess
      unavailable — baseline unavailable or source stale
    """
    if source_freshness in {"stale", "unavailable"} or baseline is None:
        return {
            "status": "unavailable",
            "source": "linkedin_own_engagement",
            "source_freshness": source_freshness,
            "confidence": 0.45,
            "evidence": [],
            "mapped_contact_ids": [],
            "mapped_thread_ids": [],
            "proposed_mutation": None,
            "display_recommendation": "suppress_unless_asked",
            "reason": (
                f"source_freshness={source_freshness}; "
                "cannot assess engagement without baseline."
            ),
        }

    name = engager.get("name") or ""
    li_url = engager.get("linkedin_url") or engager.get("urn") or ""

    if not name and not li_url:
        return {
            "status": "irrelevant",
            "source": "linkedin_own_engagement",
            "source_freshness": source_freshness,
            "confidence": 0.0,
            "evidence": [],
            "mapped_contact_ids": [],
            "mapped_thread_ids": [],
            "proposed_mutation": None,
            "display_recommendation": "suppress",
            "reason": "Engager has no name or URL; cannot assess relationship consequence.",
        }

    match = _match_engager(engager, baseline)
    if match:
        cid = match.get("id") or match.get("name")
        return {
            "status": "proposed",
            "source": "linkedin_own_engagement",
            "source_freshness": source_freshness,
            "confidence": 0.80,
            "evidence": [
                f"engager '{name}' matched baseline contact '{cid}'",
                f"event_type={event_type}",
            ],
            "mapped_contact_ids": [cid] if cid else [],
            "mapped_thread_ids": [],
            "proposed_mutation": {
                "command": "touchContact",
                "payload": {"id": cid, "source": "linkedin_own_post_engagement"},
            },
            "display_recommendation": "show",
            "reason": (
                f"Known contact '{cid}' engaged with Todd's LinkedIn post "
                f"({event_type}); touchContact may be warranted."
            ),
        }

    return {
        "status": "blocked",
        "source": "linkedin_own_engagement",
        "source_freshness": source_freshness,
        "confidence": 0.30,
        "evidence": [f"engager name='{name}' url='{li_url}' not in baseline"],
        "mapped_contact_ids": [],
        "mapped_thread_ids": [],
        "proposed_mutation": None,
        "display_recommendation": "suppress_unless_asked",
        "reason": (
            f"entity_match=none; '{name or li_url}' not found in baseline. "
            "Cannot propose RI action without a matched contact."
        ),
    }


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _urn_to_url(urn: str) -> str:
    """Convert a post URN to a LinkedIn feed update URL."""
    return f"https://www.linkedin.com/feed/update/{urn}"


def _profile_urn_to_url(urn: str) -> str | None:
    """Best-effort: convert person/company URN to profile URL."""
    if not urn:
        return None
    parts = urn.split(":")
    if len(parts) >= 4:
        entity_type = parts[2]
        entity_id = parts[3]
        if entity_type == "person":
            return f"https://www.linkedin.com/in/{entity_id}"
        if entity_type in ("organization", "company"):
            return f"https://www.linkedin.com/company/{entity_id}"
    return None


def _ms_epoch_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return None


def normalize_own_posts(
    raw_posts: list[dict[str, Any]],
    metadata: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Normalize raw LinkedIn API post elements into social.own_posts.json shape."""
    normalized: list[dict[str, Any]] = []
    metadata = metadata or {}
    for el in raw_posts:
        urn = el.get("id") or el.get("urn") or ""
        if not urn:
            continue
        text_val = el.get("commentary") or ""
        if isinstance(text_val, dict):
            text_val = text_val.get("text") or ""
        created_ms = el.get("publishedAt") or el.get("createdAt")
        posted_at = _ms_epoch_to_iso(created_ms) if isinstance(created_ms, int) else (
            created_ms if isinstance(created_ms, str) else None
        )
        meta = metadata.get(urn, {})
        counts = meta.get("socialDetail", {}) or {}
        normalized.append({
            "post_id": urn,
            "posted_at": posted_at,
            "platform": "linkedin",
            "text": text_val,
            "topics": [],
            "post_url": _urn_to_url(urn),
            "engagement_totals": {
                "likes": counts.get("totalSocialActivityCounts", {}).get("numLikes") if counts else None,
                "comments": counts.get("totalSocialActivityCounts", {}).get("numComments") if counts else None,
                "shares": counts.get("totalSocialActivityCounts", {}).get("numShares") if counts else None,
                "impressions": None,
            },
        })
    return normalized


def normalize_engagement_events(
    post_urn: str,
    reactions: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize raw API reactions + comments into social.engagement.json event shape."""
    events: list[dict[str, Any]] = []
    for r in reactions:
        actor = r.get("actor") or ""
        actor_name = ""
        if isinstance(actor, dict):
            actor_name = actor.get("localizedName") or ""
            actor = actor.get("$URN") or actor.get("urn") or ""
        at_ms = r.get("createdAt")
        events.append({
            "post_id": post_urn,
            "type": "reaction",
            "engager": {
                "name": actor_name,
                "linkedin_url": _profile_urn_to_url(actor) if isinstance(actor, str) else None,
                "urn": actor if isinstance(actor, str) else None,
            },
            "at": _ms_epoch_to_iso(at_ms) if isinstance(at_ms, int) else (at_ms or None),
            "comment_text": None,
        })
    for c in comments:
        actor = c.get("commenter") or c.get("actor") or ""
        actor_name = ""
        if isinstance(actor, dict):
            actor_name = (
                actor.get("localizedName")
                or ((actor.get("name") or {}).get("localized", {}) or {}).get("en_US", "")
                or ""
            )
            actor = actor.get("$URN") or actor.get("urn") or ""
        msg = c.get("message") or {}
        if isinstance(msg, dict):
            text = msg.get("text") or ""
        else:
            text = str(msg)
        at_ms = c.get("createdAt")
        events.append({
            "post_id": post_urn,
            "type": "comment",
            "engager": {
                "name": actor_name,
                "linkedin_url": _profile_urn_to_url(actor) if isinstance(actor, str) else None,
                "urn": actor if isinstance(actor, str) else None,
            },
            "at": _ms_epoch_to_iso(at_ms) if isinstance(at_ms, int) else (at_ms or None),
            "comment_text": text or None,
        })
    return events


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def _post_key(post: dict[str, Any]) -> str:
    return post.get("post_id") or post.get("post_url") or ""


def _event_key(ev: dict[str, Any]) -> str:
    engager = ev.get("engager") or {}
    raw = (
        str(ev.get("post_id") or "")
        + "|" + str(ev.get("type") or "")
        + "|" + str(engager.get("urn") or engager.get("linkedin_url") or engager.get("name") or "")
        + "|" + str(ev.get("at") or "")
        + "|" + str(ev.get("comment_text") or "")
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _merge_posts(
    existing: list[dict[str, Any]],
    new_posts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge new posts into existing list, deduping by post_id. New wins on conflict."""
    by_key = {_post_key(p): p for p in existing}
    for p in new_posts:
        k = _post_key(p)
        if k:
            by_key[k] = p
    return list(by_key.values())


def _merge_events(
    existing: list[dict[str, Any]],
    new_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge new events into existing list, deduping by stable hash key."""
    by_key = {_event_key(e): e for e in existing}
    for e in new_events:
        by_key[_event_key(e)] = e
    return list(by_key.values())


# ---------------------------------------------------------------------------
# Cache read / write
# ---------------------------------------------------------------------------

def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _snapshot(path: Path) -> None:
    if not path.exists():
        return
    core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snap = core.SNAPSHOTS_DIR / (
        path.stem + ".pre-ingest-" + datetime.now().strftime("%Y%m%d-%H%M%S") + path.suffix
    )
    shutil.copy2(path, snap)


def _write_caches(
    posts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    source: str,
    *,
    dry_run: bool,
    replace: bool = False,
) -> dict[str, Any]:
    """Write or preview the two social caches."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if not dry_run:
        # Merge with existing unless --replace
        if not replace:
            existing_posts = _load_cache(OWN_POSTS_PATH).get("posts") or []
            existing_events = _load_cache(ENGAGEMENT_PATH).get("events") or []
            posts = _merge_posts(existing_posts, posts)
            events = _merge_events(existing_events, events)

        _snapshot(OWN_POSTS_PATH)
        _snapshot(ENGAGEMENT_PATH)

        own_payload = {"fetched_at": now_iso, "source": source, "posts": posts}
        eng_payload = {"fetched_at": now_iso, "source": source, "events": events}

        OWN_POSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        OWN_POSTS_PATH.write_text(json.dumps(own_payload, indent=2), encoding="utf-8")
        ENGAGEMENT_PATH.write_text(json.dumps(eng_payload, indent=2), encoding="utf-8")

        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(core.PROJECT_DIR))
            except ValueError:
                return str(p)

        return {
            "ok": True,
            "dry_run": False,
            "source": source,
            "posts_written": len(posts),
            "events_written": len(events),
            "own_posts_path": _rel(OWN_POSTS_PATH),
            "engagement_path": _rel(ENGAGEMENT_PATH),
        }
    else:
        return {
            "ok": True,
            "dry_run": True,
            "source": source,
            "posts_preview": len(posts),
            "events_preview": len(events),
            "preview_own_posts": {"fetched_at": now_iso, "source": source, "posts": posts},
            "preview_engagement": {"fetched_at": now_iso, "source": source, "events": events},
        }


# ---------------------------------------------------------------------------
# Fixture mode
# ---------------------------------------------------------------------------

def run_from_fixture(
    fixture_path: Path,
    *,
    dry_run: bool,
    replace: bool = False,
) -> dict[str, Any]:
    """Load a pre-built fixture JSON and normalize it as if it came from the API."""
    if not fixture_path.exists():
        return {
            "ok": False,
            "status": "fixture_not_found",
            "path": str(fixture_path),
        }
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "fixture_parse_error", "error": str(exc)}

    raw_posts = data.get("posts") or []
    metadata = {p.get("id", ""): p.get("_metadata", {}) for p in raw_posts}
    posts = normalize_own_posts(raw_posts, metadata)

    events: list[dict[str, Any]] = []
    for post in raw_posts:
        urn = post.get("id") or post.get("urn") or ""
        events.extend(normalize_engagement_events(
            urn,
            post.get("_reactions") or [],
            post.get("_comments") or [],
        ))

    return _write_caches(posts, events, SOURCE_FIXTURE, dry_run=dry_run, replace=replace)


# ---------------------------------------------------------------------------
# Live API fetch
# ---------------------------------------------------------------------------

def run_fetch(
    *,
    dry_run: bool,
    replace: bool = False,
    post_count: int = 20,
    engagement_per_post: int = 50,
) -> dict[str, Any]:
    """Attempt live API fetch. Returns structured status on any configuration/API failure."""
    cfg = config_status()

    if not cfg["has_token"]:
        return _unavailable("not_configured", "Set LINKEDIN_ACCESS_TOKEN env var.")
    if not cfg["configured"]:
        return _unavailable(
            "missing_urn",
            "Set LINKEDIN_AUTHOR_URN (member mode) or LINKEDIN_ORGANIZATION_URN (org mode).",
        )

    token = _env("LINKEDIN_ACCESS_TOKEN")
    author_urn = _env("LINKEDIN_AUTHOR_URN")
    org_urn = _env("LINKEDIN_ORGANIZATION_URN")
    api_version = _env("LINKEDIN_API_VERSION") or DEFAULT_API_VERSION

    try:
        if cfg["mode"] == "member":
            raw_posts = fetch_author_posts(token, author_urn, api_version, count=post_count)
        else:
            raw_posts = fetch_organization_posts(token, org_urn, api_version, count=post_count)
    except HTTPError as exc:
        if exc.code == 403:
            return _unavailable("insufficient_scope", f"HTTP 403 fetching posts. Check scopes. {exc}")
        if exc.code == 401:
            return _unavailable("missing_token", f"HTTP 401 — token invalid or expired. {exc}")
        return _unavailable("http_error", f"HTTP {exc.code}: {exc}")
    except URLError as exc:
        return _unavailable("api_unavailable", f"Network error: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _unavailable("api_unavailable", str(exc))

    if not raw_posts:
        return {
            "ok": True,
            "status": "no_posts_returned",
            "posts": [],
            "events": [],
            "dry_run": dry_run,
        }

    # Fetch per-post metadata
    post_urns = [p.get("id") or "" for p in raw_posts if p.get("id")]
    try:
        metadata = fetch_social_metadata(post_urns, token, api_version)
    except Exception:  # noqa: BLE001
        metadata = {}

    posts = normalize_own_posts(raw_posts, metadata)

    # Fetch engagement per post
    events: list[dict[str, Any]] = []
    for urn in post_urns:
        reactions = fetch_reactions(urn, token, api_version, count=engagement_per_post)
        comments = fetch_comments(urn, token, api_version, count=engagement_per_post)
        events.extend(normalize_engagement_events(urn, reactions, comments))

    return _write_caches(posts, events, SOURCE_OFFICIAL, dry_run=dry_run, replace=replace)


def _unavailable(status: str, detail: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "detail": detail,
        "safe_fallback": (
            "Use LinkedIn export (linkedin_export_watcher.py) "
            "or browser-session capture (linkedin_session_reader.py --capture-own-post-engagement-js)."
        ),
        "does_not_block_delivery": True,
    }


# ---------------------------------------------------------------------------
# Smoke test (fixture-based, no network)
# ---------------------------------------------------------------------------

SMOKE_FIXTURE: dict[str, Any] = {
    "posts": [
        {
            "id": "urn:li:ugcPost:7000000000001",
            "commentary": "Multi-unit operators: the gap between POS and ops platforms is widening.",
            "publishedAt": 1747180800000,
            "_metadata": {
                "socialDetail": {
                    "totalSocialActivityCounts": {"numLikes": 47, "numComments": 12, "numShares": 3}
                }
            },
            "_reactions": [
                {
                    "actor": "urn:li:person:chason123",
                    "type": "LIKE",
                    "createdAt": 1747267200000,
                },
            ],
            "_comments": [
                {
                    "commenter": "urn:li:person:bruce456",
                    "message": {"text": "Spot on. Saw the same pattern at the McD franchisee meeting."},
                    "createdAt": 1747267200000,
                },
            ],
        },
        {
            "id": "urn:li:ugcPost:7000000000002",
            "commentary": "OTP3 leadership cohort — the value of weekly text rhythm.",
            "publishedAt": 1746576000000,
            "_metadata": {},
            "_reactions": [],
            "_comments": [],
        },
    ]
}


def _smoke() -> int:
    import tempfile
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK  " if cond else "FAIL"
        print(f"  {mark}  {msg}")
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fixture_path = tmp_path / "fixture.json"
        fixture_path.write_text(json.dumps(SMOKE_FIXTURE), encoding="utf-8")

        # Patch paths to temp dir
        orig_own = core.SOCIAL_OWN_POSTS_PATH
        orig_eng = core.SOCIAL_ENGAGEMENT_PATH
        orig_snap = core.SNAPSHOTS_DIR

        global OWN_POSTS_PATH, ENGAGEMENT_PATH
        OWN_POSTS_PATH = tmp_path / "social.own_posts.json"
        ENGAGEMENT_PATH = tmp_path / "social.engagement.json"
        core.SNAPSHOTS_DIR = tmp_path / "_snapshots"

        try:
            # 1. --status does not expose token
            st = config_status()
            ck("has_token" in st, "--status returns has_token key")
            ck("token_hint" not in st or len(st.get("token_hint") or "") < 20,
               "--status does not expose full token")

            # 2. --from-fixture --dry-run produces both normalized payloads
            result = run_from_fixture(fixture_path, dry_run=True)
            ck(result.get("ok") is True, "fixture dry-run ok")
            ck(result.get("dry_run") is True, "fixture dry-run flagged")
            ck(len(result.get("preview_own_posts", {}).get("posts") or []) == 2,
               "fixture dry-run: 2 posts previewed")
            ck(len(result.get("preview_engagement", {}).get("events") or []) == 2,
               "fixture dry-run: 2 events previewed (1 reaction + 1 comment)")

            # 3. --from-fixture --confirm writes caches
            result2 = run_from_fixture(fixture_path, dry_run=False)
            ck(result2.get("ok") is True, "fixture confirm ok")
            ck(result2.get("dry_run") is False, "fixture confirm not dry-run")
            ck(OWN_POSTS_PATH.exists(), "social.own_posts.json written")
            ck(ENGAGEMENT_PATH.exists(), "social.engagement.json written")

            own_data = json.loads(OWN_POSTS_PATH.read_text())
            eng_data = json.loads(ENGAGEMENT_PATH.read_text())
            ck(len(own_data.get("posts") or []) == 2, "own_posts: 2 posts in cache")
            ck(len(eng_data.get("events") or []) == 2, "engagement: 2 events in cache")
            ck(own_data.get("source") == SOURCE_FIXTURE, "own_posts source set correctly")

            # 4. Merging: confirm again with new post should dedupe + add
            extra_fixture = {
                "posts": [
                    {
                        "id": "urn:li:ugcPost:7000000000001",  # duplicate
                        "commentary": "Updated text",
                        "publishedAt": 1747180800000,
                        "_reactions": [], "_comments": [],
                    },
                    {
                        "id": "urn:li:ugcPost:7000000000003",  # new
                        "commentary": "Brand new post",
                        "publishedAt": 1747360000000,
                        "_reactions": [], "_comments": [],
                    },
                ]
            }
            extra_path = tmp_path / "extra.json"
            extra_path.write_text(json.dumps(extra_fixture))
            result3 = run_from_fixture(extra_path, dry_run=False)
            own3 = json.loads(OWN_POSTS_PATH.read_text())
            ck(len(own3.get("posts") or []) == 3, "merge: 2 + 1 new = 3 posts (no dup)")

            # 5. --fetch with no token returns not_configured
            saved_token = os.environ.pop("LINKEDIN_ACCESS_TOKEN", None)
            result4 = run_fetch(dry_run=True)
            if saved_token:
                os.environ["LINKEDIN_ACCESS_TOKEN"] = saved_token
            ck(result4.get("ok") is False, "no-token fetch returns ok=false")
            ck(result4.get("status") == "not_configured", "no-token status is not_configured")
            ck(result4.get("does_not_block_delivery") is True, "not_configured does not block delivery")

            # 6. fixture not found
            result5 = run_from_fixture(tmp_path / "missing.json", dry_run=True)
            ck(result5.get("ok") is False, "missing fixture returns ok=false")

        finally:
            OWN_POSTS_PATH = orig_own
            ENGAGEMENT_PATH = orig_eng
            core.SNAPSHOTS_DIR = orig_snap

    if failures:
        print(f"\nSmoke FAILED ({len(failures)} failures):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("linkedin_own_engagement smoke OK")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="LinkedIn official own-post engagement adapter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--status", action="store_true",
                   help="Report configuration status without exposing token.")
    p.add_argument("--fetch", action="store_true",
                   help="Attempt live API fetch.")
    p.add_argument("--from-fixture", metavar="PATH",
                   help="Load posts + engagement from a local fixture JSON.")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview without writing. Required unless --confirm given.")
    p.add_argument("--confirm", action="store_true",
                   help="Write normalized caches.")
    p.add_argument("--replace", action="store_true",
                   help="Replace existing caches rather than merge/dedupe.")
    p.add_argument("--post-count", type=int, default=20)
    p.add_argument("--engagement-per-post", type=int, default=50)
    p.add_argument("--smoke", action="store_true",
                   help="Run fixture-based smoke test (no network required).")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    if args.status:
        print(json.dumps(config_status(), indent=2))
        return 0

    # Require explicit --dry-run or --confirm
    if args.fetch or args.from_fixture:
        if not args.dry_run and not args.confirm:
            print("ERROR: specify --dry-run (preview) or --confirm (write)", file=sys.stderr)
            return 2
        dry_run = not args.confirm

        if args.from_fixture:
            result = run_from_fixture(
                Path(args.from_fixture),
                dry_run=dry_run,
                replace=args.replace,
            )
        else:
            result = run_fetch(
                dry_run=dry_run,
                replace=args.replace,
                post_count=args.post_count,
                engagement_per_post=args.engagement_per_post,
            )

        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") or result.get("does_not_block_delivery") else 1

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
