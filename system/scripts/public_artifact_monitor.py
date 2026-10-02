#!/usr/bin/env python3
"""Discover and retain newly linked public board/procurement documents and transcripts."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import rb_core as core

CONFIG_PATH = core.INBOX_DIR / "restaurant_tech_anticipatory_channel_watchlist.json"
STATE_PATH = core.CACHE_DIR / "public_artifact_monitor_state.json"
RESULT_PATH = core.CACHE_DIR / "public_artifact_candidates.json"
ARCHIVE_ROOT = core.SYSTEM_DIR / "artifact_vault" / "public_intelligence"
HREF_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
MATERIAL_RE = re.compile(r"\b(agenda|board|packet|minutes|procurement|solicitation|rfp|rfi|bid|transcript|webinar|podcast)\b", re.I)
DOCUMENT_RE = re.compile(r"\.(?:pdf|docx?|xlsx?)(?:$|[?#])", re.I)


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RB-PublicArtifactMonitor/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(5_000_000)


def _links(base_url: str, raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    found = {}
    for href, label_html in HREF_RE.findall(text):
        label = re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", label_html))).strip()
        url = urljoin(base_url, html.unescape(href))
        if urlparse(url).scheme not in {"http", "https"}:
            continue
        if DOCUMENT_RE.search(url) or MATERIAL_RE.search(f"{label} {url}"):
            found[url] = {"url": url, "title": label or Path(urlparse(url).path).name}
    return sorted(found.values(), key=lambda row: row["url"])


def _safe_name(url: str) -> str:
    name = Path(urlparse(url).path).name or "artifact"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]
    return f"{hashlib.sha256(url.encode()).hexdigest()[:10]}_{name}"


def run(*, today: date | None = None, fetcher=_fetch) -> dict:
    today = today or date.today()
    sources = [s for s in (_load(CONFIG_PATH).get("sources") or []) if s.get("source_category") in {"procurement_and_board_packets", "public_transcripts"}]
    old_state = _load(STATE_PATH).get("sources") or {}
    new_state, candidates, errors = {}, [], []
    for source in sources:
        url = source.get("url")
        try:
            rows = _links(url, fetcher(url))
            known = set(old_state.get(url) or [])
            current = {row["url"] for row in rows}
            if known:
                for row in rows:
                    if row["url"] in known:
                        continue
                    item = {**row, "entity": source.get("entity"), "source_url": url, "source_category": source.get("source_category"), "detected_at": today.isoformat(), "status": "pending_review"}
                    if DOCUMENT_RE.search(row["url"]):
                        try:
                            content = fetcher(row["url"])
                            folder = ARCHIVE_ROOT / today.isoformat()
                            folder.mkdir(parents=True, exist_ok=True)
                            target = folder / _safe_name(row["url"])
                            target.write_bytes(content)
                            item["archived_path"] = str(target.relative_to(core.PROJECT_DIR))
                        except Exception as exc:  # noqa: BLE001
                            item["archive_error"] = f"{type(exc).__name__}: {exc}"
                    else:
                        item["transcript_status"] = "public_text_candidate" if "transcript" in f"{row['title']} {row['url']}".lower() else "media_review_required"
                    candidates.append(item)
            new_state[url] = sorted(current)
        except Exception as exc:  # noqa: BLE001
            errors.append({"entity": source.get("entity"), "url": url, "error": f"{type(exc).__name__}: {exc}"})
            new_state[url] = old_state.get(url) or []
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"updated_at": today.isoformat(), "sources": new_state}, indent=2) + "\n", encoding="utf-8")
    result = {"contract": "rb_public_artifact_candidates_v1", "date": today.isoformat(), "sources_checked": len(sources), "new_candidates": candidates, "errors": errors, "policy": "baseline-first; newly linked documents are archived but remain pending review"}
    RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    print(json.dumps(run(today=date.fromisoformat(args.date) if args.date else None), indent=2))
