#!/usr/bin/env python3
"""Review-first monitor for newly published Franchise Disclosure Documents.

The FTC requires FDD delivery but does not operate a federal filing registry.
This monitor therefore uses the publicly readable, newest-first FDD Exchange
index as a discovery layer, matches releases to RB's restaurant-brand
watchlist, and records candidates without making canonical claims.  The
linked document remains secondary-source discovery evidence until reviewed
against an official state filing or the document itself.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.request
from urllib.parse import urljoin, urlparse
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import watchlist_registry as wr  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402

INDEX_URL = "https://fddexchange.com/fdd-library/?sort=date&dir=desc"
STATE_PATH = core.CACHE_DIR / "fdd_release_state.json"
RESULT_PATH = core.CACHE_DIR / "fdd_release_candidates.json"

ROW_RE = re.compile(
    r"<tr><td>(?P<name>.*?)</td><td>(?P<date>.*?)</td>"
    r"<td[^>]*>(?P<category>.*?)</td><td[^>]*>(?P<doc_type>.*?)</td>"
    r"<td><a[^>]+href=[\"'](?P<url>[^\"']+)[\"']",
    re.I | re.S,
)
TAG_RE = re.compile(r"<[^>]+>")
HREF_RE = re.compile(r"<a\b[^>]*\bhref=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>", re.I | re.S)
CORP_WORDS = {
    "inc", "incorporated", "llc", "ltd", "company", "co", "corp",
    "corporation", "franchise", "franchising", "restaurants", "restaurant",
    "the", "usa", "us",
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", value or ""))).strip()


def _norm(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", html.unescape(value or "").lower())
    return " ".join(word for word in words if word not in CORP_WORDS)


def parse_index(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    rows = []
    for match in ROW_RE.finditer(text):
        row = {key: _clean(value) for key, value in match.groupdict().items()}
        if row["name"] and row["date"] and row["url"]:
            rows.append(row)
    return rows


def _fetch(url: str = INDEX_URL) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RB-FDD-Monitor/1.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read(2_000_000)


def _fetch_document(url: str) -> tuple[bytes, str, str]:
    """Fetch one candidate document/page, retaining redirect and MIME evidence."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RB-FDD-Monitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        content_type = response.headers.get_content_type()
        return response.read(40_000_000), response.geturl(), content_type


def _account_for_brand(brand: str) -> tuple[str, Path] | None:
    """Resolve a watchlist brand only to an existing account with a real brief."""
    needle = _norm(brand)
    for entry in cpc.load_registry().get("registry", []):
        account_id = str(entry.get("account_id") or "")
        slug = account_id.removeprefix("acct-")
        if not slug:
            continue
        account_root = cpc.ROOT / "accounts" / slug
        names = [slug, *(entry.get("aliases") or [])]
        try:
            account = cpc.load_json(account_root / "account.json")
            names.extend([account.get("display_name"), *(account.get("aliases") or [])])
        except (OSError, ValueError, TypeError):
            pass
        if needle not in {_norm(str(name)) for name in names if name}:
            continue
        brief = account_root / "briefs" / "current" / "Background_Brief.md"
        if brief.is_file():
            return slug, account_root
    return None


def _pdf_link(page: bytes, base_url: str) -> str | None:
    text = page.decode("utf-8", errors="replace")
    ranked = []
    for match in HREF_RE.finditer(text):
        href = html.unescape(match.group("href")).strip()
        label = _clean(match.group("label")).lower()
        absolute = urljoin(base_url, href)
        score = 0
        if urlparse(absolute).path.lower().endswith(".pdf"):
            score += 10
        if "full" in label and ("fdd" in label or "disclosure document" in label):
            score += 5
        if "download" in label and ("fdd" in label or "document" in label):
            score += 3
        if score:
            ranked.append((score, absolute))
    return max(ranked, default=(0, None))[1]


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return value[:100] or "Franchise_Disclosure_Document"


def archive_fdd_for_account(candidate: dict, *, fetch_document=_fetch_document) -> dict:
    """Archive a publicly retrievable PDF; never mistake a login/HTML page for an FDD."""
    resolved = _account_for_brand(str(candidate.get("brand") or ""))
    if not resolved:
        return {"status": "skipped_no_established_background_brief"}
    slug, account_root = resolved
    source_url = str(candidate.get("source_url") or "")
    try:
        payload, final_url, content_type = fetch_document(source_url)
        document_url = final_url
        if not payload.lstrip().startswith(b"%PDF-"):
            link = _pdf_link(payload, final_url)
            if not link:
                return {"status": "blocked_no_public_pdf", "account_slug": slug}
            payload, document_url, content_type = fetch_document(link)
        if not payload.lstrip().startswith(b"%PDF-"):
            return {"status": "blocked_non_pdf_response", "account_slug": slug}
    except Exception as exc:  # network/source failures must not fail the morning cycle
        return {"status": "download_failed", "account_slug": slug, "error": str(exc)[:300]}

    digest = hashlib.sha256(payload).hexdigest()
    year_match = re.search(r"\b(20\d{2})\b", str(candidate.get("fdd_date") or ""))
    year = year_match.group(1) if year_match else "undated"
    stem = f"{year}_{_safe_filename(str(candidate.get('brand') or ''))}_FDD_{digest[:10]}"
    destination_dir = account_root / "sources" / "fdd"
    destination_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = destination_dir / f"{stem}.pdf"
    metadata_path = destination_dir / f"{stem}.json"
    pdf_path.write_bytes(payload)
    metadata_path.write_text(json.dumps({
        "contract": "rb_fdd_archive_v1",
        "account_slug": slug,
        "brand": candidate.get("brand"),
        "fdd_date": candidate.get("fdd_date"),
        "document_type": candidate.get("document_type"),
        "discovery_source_url": source_url,
        "document_url": document_url,
        "content_type": content_type,
        "sha256": digest,
        "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_status": "unreviewed_source_document",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "status": "archived",
        "account_slug": slug,
        "pdf_path": str(pdf_path.relative_to(cpc.ROOT.parent)),
        "metadata_path": str(metadata_path.relative_to(cpc.ROOT.parent)),
        "sha256": digest,
    }


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _key(row: dict) -> str:
    raw = f"{row.get('name')}|{row.get('date')}|{row.get('url')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _match_brand(name: str, brands: list[str]) -> str | None:
    needle = _norm(name)
    if not needle:
        return None
    exact = { _norm(brand): brand for brand in brands }
    if needle in exact:
        return exact[needle]
    # Require a meaningful multi-token containment match. This handles FDD
    # legal/display suffixes without letting short brands such as "Qu" or
    # "PAR" collide with unrelated names.
    for normalized, brand in exact.items():
        if len(normalized) >= 6 and (needle in normalized or normalized in needle):
            return brand
    return None


def run(*, today: date | None = None, fetcher=_fetch, document_fetcher=_fetch_document) -> dict:
    today = today or date.today()
    rows = parse_index(fetcher())
    brands = wr.mandatory_restaurant_brands()
    matched = []
    for row in rows:
        brand = _match_brand(row["name"], brands)
        if brand:
            matched.append({**row, "brand": brand, "record_key": _key(row)})

    prior = _load(STATE_PATH)
    seen = set(prior.get("seen_record_keys") or [])
    first_run = not bool(prior)
    existing_result = _load(RESULT_PATH)
    candidates = existing_result.get("candidates") or {}
    new_candidates = []
    for row in matched:
        key = row["record_key"]
        if key in seen:
            continue
        # Establish a quiet initial baseline. Subsequent new/amended documents
        # become review candidates; nothing auto-mutates account intelligence.
        if not first_run:
            candidate = {
                "candidate_id": f"fdd::{key}",
                "status": "pending_review",
                "detected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "brand": row["brand"],
                "filing_name": row["name"],
                "fdd_date": row["date"],
                "document_type": row["doc_type"],
                "category": row["category"],
                "source_name": "The FDD Exchange",
                "source_url": row["url"],
                "source_quality": "secondary_discovery",
                "required_review": "Verify against the FDD and an official state filing before recording claims.",
            }
            candidate["account_archive"] = archive_fdd_for_account(
                candidate, fetch_document=document_fetcher
            )
            candidates[candidate["candidate_id"]] = candidate
            new_candidates.append(candidate)
        seen.add(key)

    result = {
        "contract": "rb_fdd_release_monitor_v1",
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": INDEX_URL,
        "rows_scanned": len(rows),
        "watchlist_brands": len(brands),
        "matched_releases": matched,
        "first_run_baseline": first_run,
        "new_candidates": new_candidates,
        "candidates": candidates,
        "policy": "discovery_only; review and official-state verification required; no canonical auto-mutation",
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({
        "last_run": today.isoformat(),
        "seen_record_keys": sorted(seen),
    }, indent=2) + "\n", encoding="utf-8")
    RESULT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan", nargs="?")
    parser.add_argument("--date")
    args = parser.parse_args()
    result = run(today=date.fromisoformat(args.date) if args.date else None)
    print(json.dumps({
        key: result[key] for key in (
            "date", "rows_scanned", "watchlist_brands", "first_run_baseline", "new_candidates"
        )
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
