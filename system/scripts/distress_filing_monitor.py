#!/usr/bin/env python3
"""Diff official distress sources and resolve new text against tracked entities."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import urllib.request
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import rb_core as core

CONFIG_PATH = core.INBOX_DIR / "distress_public_source_watchlist.json"
ACCOUNT_REGISTRY = core.PROJECT_DIR / "customers_prospects" / "_portfolio" / "customers_prospects_registry.json"
COMPETITOR_REGISTRY = core.SYSTEM_DIR / "competitor_intelligence" / "_portfolio" / "competitor_registry.json"
STATE_PATH = core.CACHE_DIR / "distress_filing_monitor_state.json"
RESULT_PATH = core.CACHE_DIR / "distress_filing_candidates.json"


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RB-DistressMonitor/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(5_000_000).decode("utf-8", errors="replace")
    raw = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def entity_aliases() -> dict[str, list[str]]:
    entities: dict[str, set[str]] = {}
    for row in _load(ACCOUNT_REGISTRY).get("registry") or []:
        name = row.get("display_name") or row.get("account_name") or (row.get("aliases") or [None])[0]
        if name:
            entities.setdefault(name, set()).update([name, *(row.get("aliases") or [])])
    for row in _load(COMPETITOR_REGISTRY).get("registry") or []:
        name = row.get("display_name")
        if name:
            entities.setdefault(name, set()).add(name)
    return {name: sorted(a for a in aliases if len(a.strip()) >= 4) for name, aliases in entities.items()}


def _added_text(old: str, new: str) -> str:
    old_words, new_words = old.split(), new.split()
    matcher = SequenceMatcher(None, old_words, new_words, autojunk=False)
    return " ".join(" ".join(new_words[j1:j2]) for tag, _i1, _i2, j1, j2 in matcher.get_opcodes() if tag in {"insert", "replace"})


def run(*, today: date | None = None, fetcher=_fetch) -> dict:
    today = today or date.today()
    sources = _load(CONFIG_PATH).get("sources") or []
    aliases = entity_aliases()
    prior = _load(STATE_PATH).get("sources") or {}
    state, candidates, errors, baselined = {}, [], [], 0
    for source in sources:
        url = source.get("url")
        try:
            text = fetcher(url)
            digest = hashlib.sha256(text.encode()).hexdigest()
            old = prior.get(url) or {}
            if not old:
                baselined += 1
            elif old.get("sha256") != digest:
                added = _added_text(old.get("text") or "", text)
                lowered = added.lower()
                for entity, names in aliases.items():
                    hits = [name for name in names if re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", lowered)]
                    if hits:
                        candidates.append({"entity": entity, "matched_aliases": hits, "source": source.get("name"), "source_type": source.get("source_type"), "source_url": url, "detected_at": today.isoformat(), "excerpt": added[:1800], "status": "pending_review"})
            state[url] = {"sha256": digest, "text": text, "checked_at": today.isoformat()}
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": source.get("name"), "url": url, "error": f"{type(exc).__name__}: {exc}"})
            if url in prior:
                state[url] = prior[url]
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"updated_at": today.isoformat(), "sources": state}, indent=2) + "\n", encoding="utf-8")
    result = {"contract": "rb_distress_filing_candidates_v1", "date": today.isoformat(), "sources_checked": len(sources), "baselined": baselined, "new_candidates": candidates, "errors": errors, "entities_resolved": len(aliases), "policy": "name matches are pending review, never automatic distress claims"}
    RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    print(json.dumps(run(today=date.fromisoformat(args.date) if args.date else None), indent=2))
