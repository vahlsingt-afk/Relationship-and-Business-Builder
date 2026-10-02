#!/usr/bin/env python3
"""Ephemeral LinkedIn browser-session capture.

This module deliberately does not log in to LinkedIn and does not store
credentials. It accepts visible post data captured from the operator's own
logged-in browser session, stores it in a short-lived session buffer, merges it
into `system/inbox/social.feed.json` for the next daily brief, then purges
expired session-captured posts so stale LinkedIn context cannot linger.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_via_session  # noqa: E402
import rb_core as core  # noqa: E402
from employment_state import _employment_state  # noqa: E402


SESSION_CAPTURE_PATH = core.INBOX_DIR / "linkedin.session_captures.jsonl"
DEFAULT_TTL_HOURS = 36
CAPTURED_VIA = "linkedin_browser_session"
EMPLOYMENT_STATUS_SOURCE = "linkedin_profile_capture"

# ---------------------------------------------------------------------------
# Own-post engagement capture JS (Tier C)
# ---------------------------------------------------------------------------

OWN_POST_ENGAGEMENT_JS = r"""
// Paste into browser console on a LinkedIn post analytics or post detail page.
// Captures visible engagement rows (likes, reactions, comments) into a JSON payload.
// Copy the console output and save as /tmp/linkedin_engagement.json
// Then run:
//   python3 system/scripts/linkedin_session_reader.py --ingest-own-engagement \
//     --in /tmp/linkedin_engagement.json --dry-run
//   (add --confirm to write)
(() => {
  const now = new Date().toISOString();
  const postUrl = window.location.href.split('?')[0];

  // --- Reactions / likes ---
  const reactions = [...document.querySelectorAll(
    '.social-details-reactors-modal__reactor-item, ' +
    '.reactions-list__reaction, ' +
    '.social-details-social-counts__reactions-count, ' +
    '[data-test-reactions-list-item]'
  )].map(node => {
    const nameEl = node.querySelector('span[aria-hidden="true"], .actor-name, .t-bold');
    const linkEl = node.querySelector('a[href*="/in/"]');
    const reactionTypeEl = node.querySelector('[data-test-reaction-icon], .reactions-icon');
    return {
      type: 'reaction',
      name: nameEl?.innerText?.trim() || null,
      linkedin_url: linkEl?.href?.split('?')[0] || null,
      reaction_type: reactionTypeEl?.getAttribute('aria-label') || 'like',
      captured_at: now
    };
  }).filter(r => r.name);

  // --- Comments ---
  const comments = [...document.querySelectorAll(
    '.comments-comment-item, .comment-item, [data-urn*="comment"]'
  )].map(node => {
    const nameEl = node.querySelector('.comments-post-meta__name-text, .t-bold, span[aria-hidden="true"]');
    const linkEl = node.querySelector('a[href*="/in/"]');
    const textEl = node.querySelector('.comments-comment-item__main-content, .comment-text');
    const timeEl = node.querySelector('time, .comments-comment-item__timestamp');
    return {
      type: 'comment',
      name: nameEl?.innerText?.trim() || null,
      linkedin_url: linkEl?.href?.split('?')[0] || null,
      comment_text: textEl?.innerText?.trim() || null,
      posted_at: timeEl?.getAttribute('datetime') || timeEl?.innerText?.trim() || null,
      captured_at: now
    };
  }).filter(c => c.name);

  const payload = {
    source: 'linkedin_browser_session_own_post',
    captured_via: 'linkedin_browser_session_own_post',
    post_url: postUrl,
    captured_at: now,
    reactions: reactions,
    comments: comments,
    total_reactions: reactions.length,
    total_comments: comments.length
  };
  console.log(JSON.stringify(payload, null, 2));
  return payload;
})();
"""


CAPTURE_JS = r"""
(() => {
  const now = new Date().toISOString();
  const posts = [...document.querySelectorAll('[data-urn*="activity"], .feed-shared-update-v2')]
    .slice(0, 40)
    .map((node, idx) => {
      const textEl =
        node.querySelector('.feed-shared-update-v2__description, .update-components-text, [dir="ltr"]');
      const authorLink =
        node.querySelector('a[href*="/in/"], a[href*="/company/"]');
      const authorName =
        node.querySelector('.update-components-actor__name, .feed-shared-actor__name, span[aria-hidden="true"]');
      const headline =
        node.querySelector('.update-components-actor__description, .feed-shared-actor__description');
      const timeEl =
        node.querySelector('time, .update-components-actor__sub-description, .feed-shared-actor__sub-description');
      const permalink =
        node.querySelector('a[href*="/feed/update/"], a[href*="activity-"]');
      const rawText = (textEl?.innerText || '').trim();
      if (!rawText) return null;
      return {
        id: node.getAttribute('data-urn') || permalink?.href || `${authorName?.innerText || 'unknown'}::${idx}::${now}`,
        author: {
          name: (authorName?.innerText || '').replace(/\s+/g, ' ').trim(),
          linkedin_url: authorLink?.href ? authorLink.href.split('?')[0] : null,
          headline: headline?.innerText ? headline.innerText.replace(/\s+/g, ' ').trim() : null
        },
        posted_at_label: timeEl?.innerText ? timeEl.innerText.replace(/\s+/g, ' ').trim() : null,
        text: rawText,
        post_url: permalink?.href ? permalink.href.split('?')[0] : null,
        platform: 'linkedin',
        captured_at: now,
        captured_via: 'linkedin_browser_session'
      };
    })
    .filter(Boolean);
  const payload = {source: 'linkedin_browser_session', captured_via: 'linkedin_browser_session', captured_at: now, posts};
  console.log(JSON.stringify(payload, null, 2));
  return payload;
})();
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _stable_id(post: dict[str, Any]) -> str:
    return (
        post.get("id")
        or post.get("post_url")
        or f"{(post.get('author') or {}).get('name','unknown')}::{post.get('captured_at','')}"
    )


def _load_session_records(path: Path = SESSION_CAPTURE_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _write_session_records(records: list[dict[str, Any]], path: Path = SESSION_CAPTURE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if records:
        path.write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in records),
            encoding="utf-8",
        )
    elif path.exists():
        path.unlink()


def ingest(raw: dict[str, Any], *, ttl_hours: int = DEFAULT_TTL_HOURS) -> dict[str, Any]:
    normalized = fetch_via_session.normalize_social({
        **raw,
        "source": CAPTURED_VIA,
        "captured_via": CAPTURED_VIA,
    })
    expires_at = (_now() + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
    records = _load_session_records()
    by_id = {r["id"]: r for r in records if r.get("id")}
    for post in normalized.get("posts") or []:
        post["id"] = _stable_id(post)
        post["captured_via"] = CAPTURED_VIA
        by_id[post["id"]] = {
            "id": post["id"],
            "captured_at": post.get("captured_at") or normalized.get("fetched_at"),
            "expires_at": expires_at,
            "post": post,
        }
    fresh = purge_records(list(by_id.values()), now=_now())
    _write_session_records(fresh)
    materialized = materialize(records=fresh)
    return {
        "ok": True,
        "ingested": len(normalized.get("posts") or []),
        "session_records": len(fresh),
        "expires_at": expires_at,
        "social_feed_posts": materialized["post_count"],
    }


def purge_records(records: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    fresh: list[dict[str, Any]] = []
    for record in records:
        expires = _parse_dt(record.get("expires_at"))
        if expires and expires < now:
            continue
        fresh.append(record)
    return fresh


def purge(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    before = _load_session_records()
    after = purge_records(before, now=now)
    _write_session_records(after)
    materialized = materialize(records=after)
    return {
        "ok": True,
        "purged": len(before) - len(after),
        "remaining_session_records": len(after),
        "social_feed_posts": materialized["post_count"],
    }


def _load_social_feed() -> dict[str, Any]:
    if not core.SOCIAL_FEED_PATH.exists():
        return {"fetched_at": None, "source": "mixed", "posts": []}
    try:
        return json.loads(core.SOCIAL_FEED_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"fetched_at": None, "source": "mixed", "posts": []}


def materialize(*, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Merge non-expired session captures into social.feed.json.

    Non-session posts already in social.feed.json are preserved. Expired
    session-captured posts are removed from the materialized feed.
    """
    records = records if records is not None else purge_records(_load_session_records(), now=_now())
    existing = _load_social_feed()
    posts_by_id: dict[str, dict[str, Any]] = {}
    for post in existing.get("posts") or []:
        if post.get("captured_via") == CAPTURED_VIA:
            continue
        posts_by_id[_stable_id(post)] = post
    for record in records:
        post = record.get("post") or {}
        if not post:
            continue
        posts_by_id[_stable_id(post)] = post
    out = {
        "fetched_at": _now().isoformat(timespec="seconds"),
        "source": "mixed_with_linkedin_browser_session",
        "session_capture_ttl_hours": DEFAULT_TTL_HOURS,
        "posts": list(posts_by_id.values()),
    }
    core.SOCIAL_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    core.SOCIAL_FEED_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "post_count": len(out["posts"])}


def status() -> dict[str, Any]:
    records = _load_session_records()
    now = _now()
    expired = [r for r in records if (_parse_dt(r.get("expires_at")) or now) < now]
    return {
        "session_capture_path": str(SESSION_CAPTURE_PATH.relative_to(core.PROJECT_DIR)),
        "social_feed_path": str(core.SOCIAL_FEED_PATH.relative_to(core.PROJECT_DIR)),
        "records": len(records),
        "expired": len(expired),
        "ttl_hours": DEFAULT_TTL_HOURS,
        "credential_policy": "uses existing browser session only; never store LinkedIn password",
    }


# ---------------------------------------------------------------------------
# Own-post engagement ingest (Tier C)
# ---------------------------------------------------------------------------

def _load_own_posts() -> dict[str, Any]:
    if not core.SOCIAL_OWN_POSTS_PATH.exists():
        return {"fetched_at": None, "source": "mixed", "posts": []}
    try:
        return json.loads(core.SOCIAL_OWN_POSTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"fetched_at": None, "source": "mixed", "posts": []}


def _load_engagement() -> dict[str, Any]:
    if not core.SOCIAL_ENGAGEMENT_PATH.exists():
        return {"fetched_at": None, "source": "mixed", "events": []}
    try:
        return json.loads(core.SOCIAL_ENGAGEMENT_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"fetched_at": None, "source": "mixed", "events": []}


def _event_key(ev: dict[str, Any]) -> str:
    import hashlib
    engager = ev.get("engager") or {}
    raw = (
        str(ev.get("post_id") or "")
        + "|" + str(ev.get("type") or "")
        + "|" + str(engager.get("linkedin_url") or engager.get("name") or "")
        + "|" + str(ev.get("comment_text") or "")
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def ingest_own_engagement(
    raw: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Normalize a browser-captured own-post engagement payload and merge into caches.

    Operator safety:
    - Never logs in to LinkedIn.
    - Only processes data from pages the operator manually opened.
    - Always requires explicit --confirm for writes.
    """
    now_iso = _now().isoformat(timespec="seconds")
    post_url = raw.get("post_url") or ""
    captured_at = raw.get("captured_at") or now_iso
    source = "linkedin_browser_session_own_post"

    events: list[dict[str, Any]] = []

    for r in raw.get("reactions") or []:
        events.append({
            "post_id": post_url,
            "type": "reaction",
            "engager": {
                "name": r.get("name") or "",
                "linkedin_url": r.get("linkedin_url"),
                "urn": None,
            },
            "at": r.get("captured_at") or captured_at,
            "comment_text": None,
        })

    for c in raw.get("comments") or []:
        events.append({
            "post_id": post_url,
            "type": "comment",
            "engager": {
                "name": c.get("name") or "",
                "linkedin_url": c.get("linkedin_url"),
                "urn": None,
            },
            "at": c.get("posted_at") or c.get("captured_at") or captured_at,
            "comment_text": c.get("comment_text"),
        })

    new_event_count = len(events)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "post_url": post_url,
            "events_preview": new_event_count,
            "preview_events": events,
        }

    # Merge into existing engagement cache
    existing = _load_engagement()
    existing_events = existing.get("events") or []
    by_key = {_event_key(e): e for e in existing_events}
    for e in events:
        by_key[_event_key(e)] = e
    merged = list(by_key.values())

    import shutil as _shutil
    if core.SOCIAL_ENGAGEMENT_PATH.exists():
        core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snap = core.SNAPSHOTS_DIR / (
            "social.engagement.pre-session-ingest-"
            + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
        )
        _shutil.copy2(core.SOCIAL_ENGAGEMENT_PATH, snap)

    eng_payload = {
        "fetched_at": now_iso,
        "source": source,
        "events": merged,
    }
    core.SOCIAL_ENGAGEMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    core.SOCIAL_ENGAGEMENT_PATH.write_text(json.dumps(eng_payload, indent=2), encoding="utf-8")

    # Update or add post entry in social.own_posts.json
    _new_totals = {
        "likes": sum(1 for e in events if e["type"] in ("reaction", "like")),
        "comments": sum(1 for e in events if e["type"] == "comment"),
        "shares": None,
        "impressions": None,
    }

    own_data = _load_own_posts()
    own_posts = own_data.get("posts") or []
    existing_post = next(
        (p for p in own_posts if (p.get("post_id") or p.get("post_url")) == post_url),
        None,
    ) if post_url else None

    own_updated = False
    if post_url and existing_post is not None:
        # Post already exists — refresh engagement_totals, fetched_at, source so
        # linkedin_own_posts is not left stale after a successful browser capture.
        existing_post["engagement_totals"] = _new_totals
        # Only overwrite text/posted_at if the existing values are blank.
        if not existing_post.get("posted_at"):
            existing_post["posted_at"] = None
        own_updated = True
    elif post_url:
        own_posts.append({
            "post_id": post_url,
            "posted_at": None,
            "platform": "linkedin",
            "text": None,
            "topics": [],
            "post_url": post_url,
            "engagement_totals": _new_totals,
        })
        own_updated = True

    if own_updated:
        own_payload = {"fetched_at": now_iso, "source": source, "posts": own_posts}
        core.SOCIAL_OWN_POSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.SOCIAL_OWN_POSTS_PATH.write_text(json.dumps(own_payload, indent=2), encoding="utf-8")

    def _rel(p: "Path") -> str:
        try:
            return str(p.relative_to(core.PROJECT_DIR))
        except ValueError:
            return str(p)

    return {
        "ok": True,
        "dry_run": False,
        "post_url": post_url,
        "events_written": len(merged),
        "new_events": new_event_count,
        "own_posts_updated": own_updated,
        "engagement_path": _rel(core.SOCIAL_ENGAGEMENT_PATH),
        "own_posts_path": _rel(core.SOCIAL_OWN_POSTS_PATH),
    }


PROFILE_CAPTURE_JS = r"""
// Paste into browser console while on a LinkedIn profile page (linkedin.com/in/<slug>)
// OR on the person's activity page (linkedin.com/in/<slug>/recent-activity/shares/).
//
// Captures in ONE paste:
//   - Static profile: name, headline, location, about, experience, education,
//     skills, mutual connections, contact links
//   - Recent activity: up to 20 of the person's posts visible on the page
//
// Copy the full console output (the JSON object), save to a file, then run:
//   python3 system/scripts/linkedin_session_reader.py --ingest-profile \
//     --in /tmp/linkedin_profile.json --dry-run
//   (replace --dry-run with --confirm to write)
//
// TIP: For more post coverage, first capture the profile page (gets static data
// + a few pinned/recent posts), then navigate to
//   linkedin.com/in/<slug>/recent-activity/shares/
// and paste again. The --ingest-profile command merges both runs.
(() => {
  const now = new Date().toISOString();
  const pageUrl = window.location.href;
  const profileUrl = pageUrl.replace(/\?.*/, '').replace(/\/$/, '');
  const slugMatch = profileUrl.match(/linkedin\.com\/in\/([^\/\?#]+)/);
  const slug = slugMatch ? slugMatch[1] : null;
  const isActivityPage = /\/recent-activity\//.test(pageUrl);

  // =========================================================================
  // PROFILE FIELDS (only meaningful on the main profile page)
  // =========================================================================

  // --- Name ---
  const nameEl = document.querySelector(
    'h1.text-heading-xlarge, h1[class*="heading"], ' +
    '.pv-text-details__left-panel h1, .artdeco-entity-lockup__title h1'
  ) || (!isActivityPage ? document.querySelector('h1') : null);
  const name = nameEl?.innerText?.trim() || null;

  // --- Headline ---
  const headlineEl = document.querySelector(
    '.text-body-medium.break-words, ' +
    '.pv-text-details__left-panel .text-body-medium, ' +
    '.artdeco-entity-lockup__subtitle'
  );
  const headline = headlineEl?.innerText?.trim() || null;

  // --- Location ---
  const locationEl = document.querySelector(
    '.text-body-small.inline.t-black--light.break-words, ' +
    '.pv-text-details__left-panel .t-black--light.t-normal.inline-block, ' +
    '[class*="profile-section-card__location"]'
  );
  const location = locationEl?.innerText?.trim() || null;

  // --- About ---
  const aboutAnchor = document.querySelector('#about');
  let about = null;
  if (aboutAnchor) {
    const sect = aboutAnchor.closest('section') || aboutAnchor.parentElement?.parentElement;
    const textEls = sect?.querySelectorAll('span[aria-hidden="true"]') || [];
    const longest = [...textEls].reduce((a, b) =>
      (b.innerText?.length || 0) > (a.innerText?.length || 0) ? b : a, {innerText: ''});
    about = longest.innerText?.trim() || null;
  }

  // --- Helper: extract pvs-list items from a section anchor id ---
  function extractListSection(anchorId) {
    const anchor = document.querySelector('#' + anchorId);
    if (!anchor) return [];
    const container = anchor.closest('section') || anchor.parentElement?.parentElement;
    return [...(container?.querySelectorAll('li.artdeco-list__item, li[class*="pvs-list__item"]') || [])];
  }

  // --- Helper: get all aria-hidden span texts from an element ---
  function ariaTexts(el) {
    return [...(el?.querySelectorAll('span[aria-hidden="true"]') || [])]
      .map(s => s.innerText?.trim()).filter(Boolean);
  }

  // --- Experience ---
  const experience = extractListSection('experience').map(item => {
    const texts = ariaTexts(item);
    const datesEl = item.querySelector('.t-black--light.t-normal span[aria-hidden="true"]');
    return {
      title: texts[0] || null,
      company: texts[1] || null,
      dates: datesEl?.innerText?.trim() || texts[2] || null,
      description: texts.slice(3).join(' ') || null,
    };
  }).filter(e => e.title);

  // --- Education ---
  const education = extractListSection('education').map(item => {
    const texts = ariaTexts(item);
    const datesEl = item.querySelector('.t-black--light.t-normal span[aria-hidden="true"]');
    return {
      school: texts[0] || null,
      degree: texts[1] || null,
      dates: datesEl?.innerText?.trim() || texts[2] || null,
    };
  }).filter(e => e.school);

  // --- Skills ---
  const skills = extractListSection('skills').slice(0, 20).map(item => {
    const texts = ariaTexts(item);
    return texts[0] || null;
  }).filter(Boolean);

  // --- Mutual connections ---
  const mutualEl = document.querySelector(
    '[data-test-app-aware-link*="mutual"] span, ' +
    '.pv-text-details__separator ~ span, ' +
    '.pv-member-badge ~ span'
  );
  const mutual_connections_text = mutualEl?.innerText?.trim() || null;

  // --- Contact info (visible without clicking) ---
  const contactLinks = [...document.querySelectorAll(
    '.pv-contact-info__contact-type a, .ci-email a, .ci-phone a, ' +
    '[class*="contact-info"] a[href^="mailto:"], [class*="contact-info"] a[href^="tel:"], ' +
    '[class*="contact-info"] a[href^="http"]'
  )].map(a => ({ href: a.href.split('?')[0], text: a.innerText?.trim() }))
    .filter(c => c.href && !c.href.includes('linkedin.com/in/'));

  // =========================================================================
  // RECENT POSTS (works on both profile page and /recent-activity/ page)
  // =========================================================================
  const postNodes = [...document.querySelectorAll(
    '[data-urn*="activity"], .feed-shared-update-v2, ' +
    '[class*="occludable-update"], [data-view-name="feed-full-update"]'
  )].slice(0, 20);

  const posts = postNodes.map((node, idx) => {
    const textEl = node.querySelector(
      '.feed-shared-update-v2__description, .update-components-text, ' +
      '[data-test-id="main-feed-activity-card__commentary"], [dir="ltr"]'
    );
    const authorLink = node.querySelector('a[href*="/in/"], a[href*="/company/"]');
    const authorNameEl = node.querySelector(
      '.update-components-actor__name span[aria-hidden="true"], ' +
      '.feed-shared-actor__name, .update-components-actor__name'
    );
    const headlineEl2 = node.querySelector(
      '.update-components-actor__description, .feed-shared-actor__description'
    );
    const timeEl = node.querySelector('time, [class*="actor__sub-description"] span[aria-hidden="true"]');
    const permalink = node.querySelector('a[href*="/feed/update/"], a[href*="activity-"]');
    const rawText = (textEl?.innerText || '').trim();
    if (!rawText) return null;
    return {
      id: node.getAttribute('data-urn') || permalink?.href?.split('?')[0] ||
          `${slug || 'unknown'}::${idx}::${now}`,
      author: {
        name: (authorNameEl?.innerText || name || '').replace(/\s+/g, ' ').trim() || null,
        linkedin_url: authorLink?.href ? authorLink.href.split('?')[0] : (slug ? `https://www.linkedin.com/in/${slug}` : null),
        headline: headlineEl2?.innerText?.trim() || headline || null,
      },
      posted_at_label: timeEl?.getAttribute('datetime') || timeEl?.innerText?.trim() || null,
      text: rawText,
      post_url: permalink?.href ? permalink.href.split('?')[0] : null,
      platform: 'linkedin',
      captured_at: now,
      captured_via: 'linkedin_browser_session_profile',
    };
  }).filter(Boolean);

  // =========================================================================
  // PAYLOAD
  // =========================================================================
  const payload = {
    source: 'linkedin_browser_session_profile',
    captured_via: 'linkedin_browser_session_profile',
    captured_at: now,
    profile_url: profileUrl,
    slug,
    is_activity_page: isActivityPage,
    // Profile fields (populated on main profile page)
    name,
    headline,
    location,
    about,
    experience,
    education,
    skills,
    mutual_connections_text,
    contact_links: contactLinks,
    // Activity
    posts,
    posts_captured: posts.length,
  };
  console.log(JSON.stringify(payload, null, 2));
  return payload;
})();
"""


def _extract_current_position(experience: list[dict]) -> tuple[str | None, str | None]:
    """Compatibility wrapper returning the date-aware active position."""
    state = _employment_state(experience)
    return state["current_company"], state["current_role"]


def ingest_profile(
    raw: dict,
    *,
    dry_run: bool,
) -> dict:
    """Normalize a browser-captured LinkedIn profile and enrich the matching baseline contact.

    Operator safety:
    - Never logs in to LinkedIn.
    - Only processes data from pages the operator manually opened.
    - Writes only to baseline_index.json; always requires --confirm for writes.

    Resolution: matches the profile slug or name against existing contacts using
    the same logic as the resolveLinkedInProfile endpoint in server.py. On match,
    enriches company/role/headline/about/experience/education fields while
    preserving all manually-curated RB fields (circles, tags, relationship_type, etc.).
    On no-match, creates a stub contact for manual-intake.
    """
    import re

    now_iso = _now().isoformat(timespec="seconds")
    profile_url = raw.get("profile_url") or ""
    slug = raw.get("slug") or ""
    if not slug and profile_url:
        m = re.search(r"linkedin\.com/in/([^/?#]+)", profile_url)
        if m:
            slug = m.group(1).rstrip("/").lower()

    name = raw.get("name") or ""
    headline = raw.get("headline") or ""
    location = raw.get("location") or ""
    about = raw.get("about") or ""
    experience = raw.get("experience") or []
    education = raw.get("education") or []
    skills = raw.get("skills") or []
    posts = raw.get("posts") or []
    employment = _employment_state(experience)
    current_company = employment["current_company"]
    current_role = employment["current_role"]

    # Resolve against baseline
    baseline = core.load_baseline()
    matched_contact: dict | None = None
    match_reason = ""

    # 1. Slug match against linkedin_url field
    if slug:
        slug_lower = slug.lower()
        for c in baseline:
            li_url = (c.get("linkedin_url") or "").lower()
            if not li_url:
                continue
            m = re.search(r"linkedin\.com/in/([^/?#]+)", li_url)
            if m and m.group(1).rstrip("/").lower() == slug_lower:
                matched_contact = c
                match_reason = "linkedin_url_slug"
                break

    # 2. Name match fallback
    if not matched_contact and name:
        name_lower = name.lower().strip()
        for c in baseline:
            if (c.get("name") or "").lower().strip() == name_lower:
                matched_contact = c
                match_reason = "name_exact"
                break

    preview = {
        "profile_url": profile_url,
        "slug": slug,
        "name": name,
        "headline": headline,
        "current_company": current_company,
        "current_role": current_role,
        "employment_status": employment["status"],
        "last_known_company": employment["last_known_company"],
        "last_known_role": employment["last_known_role"],
        "last_known_dates": employment["last_known_dates"],
        "employment_status_source": EMPLOYMENT_STATUS_SOURCE,
        "employment_status_observed_at": now_iso,
        "employment_end_date": employment["employment_end_date"],
        "employment_date_confidence": employment["employment_date_confidence"],
        "experience_count": len(experience),
        "education_count": len(education),
        "skills_count": len(skills),
        "posts_captured": len(posts),
    }

    if dry_run:
        if matched_contact:
            return {
                "ok": True,
                "dry_run": True,
                "status": "matched",
                "match_reason": match_reason,
                "matched_contact_id": matched_contact.get("id"),
                "matched_contact_name": matched_contact.get("name"),
                "preview": preview,
                "fields_to_update": {
                    "linkedin_url": profile_url or matched_contact.get("linkedin_url"),
                    "headline": headline or matched_contact.get("headline"),
                    "current_company": (None if employment["definitive_no_current_role"]
                                        else current_company or matched_contact.get("current_company")),
                    "current_role": (None if employment["definitive_no_current_role"]
                                     else current_role or matched_contact.get("current_role")),
                    "employment_status": employment["status"],
                    "last_known_company": employment["last_known_company"],
                    "last_known_role": employment["last_known_role"],
                    "last_known_dates": employment["last_known_dates"],
                    "location": location or matched_contact.get("location"),
                },
                "posts_would_ingest": len(posts),
            }
        else:
            return {
                "ok": True,
                "dry_run": True,
                "status": "no_match",
                "preview": preview,
                "action": "would create stub contact for manual-intake",
                "posts_would_ingest": len(posts),
            }

    # --- Writes ---
    import copy
    if matched_contact:
        # Enrich: fill blank fields, preserve non-blank curated values, update linkedin_url and career
        idx = next(i for i, c in enumerate(baseline) if c.get("id") == matched_contact.get("id"))
        updated = copy.deepcopy(baseline[idx])

        # Always update linkedin_url if we have a confirmed slug
        if profile_url:
            updated["linkedin_url"] = profile_url
        # Overwrite headline/company/role with fresh data (LinkedIn is authoritative for these)
        if headline:
            updated["headline"] = headline
        if employment["definitive_no_current_role"]:
            updated["current_company"] = None
            updated["current_role"] = None
        elif current_company:
            updated["current_company"] = current_company
        if current_role and not employment["definitive_no_current_role"]:
            updated["current_role"] = current_role
        updated["employment_status"] = employment["status"]
        updated["employment_status_source"] = EMPLOYMENT_STATUS_SOURCE
        updated["employment_status_observed_at"] = now_iso
        updated["employment_end_date"] = employment["employment_end_date"]
        updated["employment_date_confidence"] = employment["employment_date_confidence"]
        tags = updated.setdefault("tags", [])
        if employment["definitive_no_current_role"]:
            if "linkedin_no_stated_current_role" not in tags:
                tags.append("linkedin_no_stated_current_role")
        elif "linkedin_no_stated_current_role" in tags:
            tags.remove("linkedin_no_stated_current_role")
        if employment["last_known_company"]:
            updated["last_known_company"] = employment["last_known_company"]
        if employment["last_known_role"]:
            updated["last_known_role"] = employment["last_known_role"]
        if employment["last_known_dates"]:
            updated["last_known_role_dates"] = employment["last_known_dates"]
        # Fill blanks only
        if location and not updated.get("location"):
            updated["location"] = location
        if about and not updated.get("about"):
            updated["about"] = about
        if experience:
            updated["linkedin_experience"] = experience
        if education:
            updated["linkedin_education"] = education
        if skills:
            updated["linkedin_skills"] = skills
        updated["linkedin_profile_captured_at"] = now_iso

        baseline[idx] = updated
        core.BASELINE_PATH.write_text(
            json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
        )

        # Ingest any captured posts into the social feed
        posts_result = None
        if posts:
            posts_result = ingest({
                "source": "linkedin_browser_session_profile",
                "captured_via": "linkedin_browser_session_profile",
                "captured_at": now_iso,
                "posts": posts,
            })

        return {
            "ok": True,
            "dry_run": False,
            "status": "matched_and_enriched",
            "match_reason": match_reason,
            "contact_id": updated.get("id"),
            "contact_name": updated.get("name"),
            "fields_updated": ["linkedin_url", "headline", "current_company",
                               "current_role", "employment_status", "employment_status_source",
                               "employment_status_observed_at", "employment_end_date",
                               "employment_date_confidence", "last_known_company",
                               "last_known_role", "last_known_role_dates",
                               "linkedin_experience", "linkedin_education", "linkedin_skills"],
            "career_support_candidate": employment["definitive_no_current_role"],
            "career_support_reason": (
                "LinkedIn shows the last listed role has ended and no current role is stated."
                if employment["definitive_no_current_role"] else None
            ),
            "posts_ingested": posts_result.get("ingested", 0) if posts_result else 0,
        }
    else:
        # Create stub contact
        import hashlib
        stub_id = "li-" + hashlib.sha1((slug or name or now_iso).encode()).hexdigest()[:12]
        stub = {
            "id": stub_id,
            "name": name,
            "linkedin_url": profile_url,
            "headline": headline,
            "current_company": current_company,
            "current_role": current_role,
            "employment_status": employment["status"],
            "employment_status_source": EMPLOYMENT_STATUS_SOURCE,
            "employment_status_observed_at": now_iso,
            "employment_end_date": employment["employment_end_date"],
            "employment_date_confidence": employment["employment_date_confidence"],
            "last_known_company": employment["last_known_company"],
            "last_known_role": employment["last_known_role"],
            "last_known_role_dates": employment["last_known_dates"],
            "tags": (["linkedin_no_stated_current_role"]
                     if employment["definitive_no_current_role"] else []),
            "location": location,
            "about": about,
            "linkedin_experience": experience,
            "linkedin_education": education,
            "linkedin_skills": skills,
            "linkedin_profile_captured_at": now_iso,
            "_stub": True,
            "_stub_source": "linkedin_profile_capture",
            "_stub_note": "Created from LinkedIn profile browser capture. Run manual relationship intake to enrich.",
        }
        baseline.append(stub)
        core.BASELINE_PATH.write_text(
            json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
        )

        # Ingest any captured posts into the social feed
        posts_result = None
        if posts:
            posts_result = ingest({
                "source": "linkedin_browser_session_profile",
                "captured_via": "linkedin_browser_session_profile",
                "captured_at": now_iso,
                "posts": posts,
            })

        return {
            "ok": True,
            "dry_run": False,
            "status": "stub_created",
            "contact_id": stub_id,
            "contact_name": name,
            "career_support_candidate": employment["definitive_no_current_role"],
            "posts_ingested": posts_result.get("ingested", 0) if posts_result else 0,
            "next_step": "Run manualRelationshipIntake to add relationship context, circles, and trust signals.",
        }


def self_test() -> int:
    import tempfile
    original_inbox = core.INBOX_DIR
    original_social = core.SOCIAL_FEED_PATH
    global SESSION_CAPTURE_PATH
    original_session = SESSION_CAPTURE_PATH
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        core.INBOX_DIR = tmp_path  # type: ignore
        core.SOCIAL_FEED_PATH = tmp_path / "social.feed.json"  # type: ignore
        SESSION_CAPTURE_PATH = tmp_path / "linkedin.session_captures.jsonl"
        try:
            result = ingest({
                "posts": [{
                    "id": "p1",
                    "author": {"name": "Sarah McAngus", "linkedin_url": "https://www.linkedin.com/in/sarah-mcangus"},
                    "text": "Foods Connected is expanding food safety traceability in North America.",
                    "captured_at": "2026-05-21T12:00:00+00:00",
                }]
            }, ttl_hours=1)
            feed = json.loads(core.SOCIAL_FEED_PATH.read_text())
            ok_ingest = result["session_records"] == 1 and len(feed["posts"]) == 1
            future = _now() + timedelta(hours=2)
            result2 = purge(now=future)
            feed2 = json.loads(core.SOCIAL_FEED_PATH.read_text())
            ok_purge = result2["purged"] == 1 and len(feed2["posts"]) == 0
            if not (ok_ingest and ok_purge):
                print(json.dumps({"ok_ingest": ok_ingest, "ok_purge": ok_purge, "result": result, "purge": result2}, indent=2))
                return 1
            print("linkedin_session_reader self-test OK")
            return 0
        finally:
            core.INBOX_DIR = original_inbox  # type: ignore
            core.SOCIAL_FEED_PATH = original_social  # type: ignore
            SESSION_CAPTURE_PATH = original_session


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ingest", action="store_true", help="Read captured feed JSON from --in or stdin.")
    p.add_argument("--ingest-own-engagement", action="store_true",
                   help="Ingest own-post engagement capture from --in. Requires --dry-run or --confirm.")
    p.add_argument("--in", dest="infile", help="Raw browser-capture JSON file. Defaults to stdin.")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing (for --ingest-own-engagement).")
    p.add_argument("--confirm", action="store_true", help="Write caches (for --ingest-own-engagement).")
    p.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS)
    p.add_argument("--purge", action="store_true", help="Purge expired session captures and materialize feed.")
    p.add_argument("--materialize", action="store_true", help="Materialize current non-expired session captures.")
    p.add_argument("--status", action="store_true")
    p.add_argument("--capture-js", action="store_true", help="Print feed capture JavaScript snippet.")
    p.add_argument("--capture-own-post-engagement-js", action="store_true",
                   help="Print JavaScript to capture engagement from a LinkedIn post analytics page.")
    p.add_argument("--capture-profile-js", action="store_true",
                   help="Print JavaScript to capture a LinkedIn profile page (navigate to "
                        "linkedin.com/in/<slug>, paste the snippet in the browser console, "
                        "copy the output, then run --ingest-profile --in /tmp/linkedin_profile.json).")
    p.add_argument("--ingest-profile", action="store_true",
                   help="Ingest a LinkedIn profile capture from --in. Requires --dry-run or --confirm.")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    if args.capture_js:
        print(CAPTURE_JS.strip())
        return 0
    if args.capture_profile_js:
        print(PROFILE_CAPTURE_JS.strip())
        return 0
    if args.ingest_profile:
        if not args.dry_run and not args.confirm:
            print("ERROR: specify --dry-run (preview) or --confirm (write)", file=sys.stderr)
            return 2
        dry_run = not args.confirm
        raw_text = Path(args.infile).read_text(encoding="utf-8") if args.infile else sys.stdin.read()
        result = ingest_profile(json.loads(raw_text), dry_run=dry_run)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if args.capture_own_post_engagement_js:
        print(OWN_POST_ENGAGEMENT_JS.strip())
        return 0
    if args.self_test:
        return self_test()
    if args.status:
        print(json.dumps(status(), indent=2))
        return 0
    if args.purge:
        print(json.dumps(purge(), indent=2))
        return 0
    if args.materialize:
        print(json.dumps(materialize(), indent=2))
        return 0
    if args.ingest:
        raw_text = Path(args.infile).read_text(encoding="utf-8") if args.infile else sys.stdin.read()
        print(json.dumps(ingest(json.loads(raw_text), ttl_hours=args.ttl_hours), indent=2))
        return 0
    if args.ingest_own_engagement:
        if not args.dry_run and not args.confirm:
            print("ERROR: specify --dry-run (preview) or --confirm (write)", file=sys.stderr)
            return 2
        dry_run = not args.confirm
        raw_text = Path(args.infile).read_text(encoding="utf-8") if args.infile else sys.stdin.read()
        result = ingest_own_engagement(json.loads(raw_text), dry_run=dry_run)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
