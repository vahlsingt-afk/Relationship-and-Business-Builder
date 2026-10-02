#!/usr/bin/env python3
"""Process new normalized LinkedIn feed posts through the mutation engine."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import rb_core as core
import intelligence_mutation_engine


FEED_PATH = core.INBOX_DIR / "social.feed.json"
MANIFEST_PATH = core.CACHE_DIR / "social_content_mutation.json"


def _hash_post(post: dict) -> str:
    stable = post.get("post_url") or post.get("id") or post.get("text") or ""
    return hashlib.sha256(str(stable).encode()).hexdigest()[:24]


def _load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def process_new(*, dry_run: bool = False) -> dict:
    feed = _load_json(FEED_PATH, {"posts": []})
    manifest = _load_json(MANIFEST_PATH, {"processed": {}})
    processed = manifest.setdefault("processed", {})
    results = []
    skipped = []

    for post in feed.get("posts") or []:
        text = str(post.get("text") or "").strip()
        post_hash = _hash_post(post)
        if len(text) < 80:
            skipped.append({"hash": post_hash, "reason": "insufficient_content"})
            continue
        if post_hash in processed:
            skipped.append({"hash": post_hash, "reason": "already_processed"})
            continue
        author = post.get("author") or {}
        result = intelligence_mutation_engine.run(
            text,
            source_title=f"LinkedIn post by {author.get('name') or 'unknown author'}",
            source_url=post.get("post_url") or post.get("id") or "",
            source_date=post.get("posted_at") or "",
            source_author_name=author.get("name") or "",
            source_author_org=author.get("company") or "",
            source_author_role=author.get("headline") or "",
            auto_apply=not dry_run,
            dry_run=dry_run,
        )
        results.append({
            "hash": post_hash,
            "post_url": post.get("post_url") or post.get("id"),
            "author": author.get("name"),
            "mutation_result": result,
        })
        if not dry_run:
            processed[post_hash] = {
                "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "post_url": post.get("post_url") or post.get("id"),
                "author": author.get("name"),
            }

    if not dry_run:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    return {
        "ok": True,
        "dry_run": dry_run,
        "processed_count": len(results),
        "skipped_count": len(skipped),
        "results": results,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm:
        parser.error("specify --dry-run or --confirm")
    result = process_new(dry_run=not args.confirm)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
