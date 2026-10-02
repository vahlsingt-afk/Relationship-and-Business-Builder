#!/usr/bin/env python3
"""url_content_fetcher.py — real, deterministic webpage fetch + extraction.

RB-2026-09-01. Backs rbb_chat.py's fetchUrlContent tool: pasting a URL into
chat previously did nothing (ingestContent/processMacroSignal only ever
accept raw text; neither has a field that gets dereferenced as a URL — the
model only ever saw the literal link string).

This module is the honest, deterministic fix — same discipline as the
2026-08-27 ingestContent auto_persist incident: when tool_choice="required"
forces the model to call something it has no real tool for, it fabricates.
The fetch here is real server code, never something the model asserts, and
every failure mode returns a specific, honest reason -- never a silent
"success" with garbage, and never left for the model to paper over with a
guess from its own training knowledge.

No new dependencies: httpx and lxml are already vendored in the real
production LaunchAgent environment (confirmed against
~/Library/Application Support/Relationship Builder/vendor/py39/).

CLI (for manual live testing):
    python3 system/scripts/url_content_fetcher.py <url>
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import httpx
except ImportError:
    httpx = None  # noqa: N816 — caller sees a clear ok=False reason instead of an ImportError

try:
    from lxml import html as lxml_html
except ImportError:
    lxml_html = None

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT_SECONDS = 15
MIN_READABLE_CHARS = 200  # below this, treat as a gate/JS-shell/consent page, not a real article

# Domains known, from direct testing, to block anonymous fetches (login
# wall / bot-check) -- short-circuited before wasting a real HTTP round
# trip on a fetch we already know will fail. Not an exhaustive list; add
# to it as real failures are confirmed, not speculatively.
GATED_DOMAINS = {
    "linkedin.com",
    "www.linkedin.com",
}

_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form", "svg", "noscript")


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _is_gated_domain(url: str) -> bool:
    host = _domain(url)
    return any(host == d or host.endswith("." + d) for d in GATED_DOMAINS)


def _extract_readable_text(html_bytes: bytes, base_url: str) -> tuple[str, str]:
    """Returns (title, article_text). Lightweight, dependency-free
    approximation of what readability.js/trafilatura do: strip
    boilerplate tags, prefer <article>/<main>, else the element with the
    most cumulative <p> text. Real DOM-based extraction (lxml), not the
    regex tag-stripping this codebase's existing RSS scanners use
    elsewhere for short title/description fields -- a full article page
    needs a real parser to get right."""
    doc = lxml_html.fromstring(html_bytes, base_url=base_url)

    title_els = doc.xpath("//title")
    title = title_els[0].text_content().strip() if title_els else ""

    for tag in _STRIP_TAGS:
        for el in doc.xpath(f"//{tag}"):
            el.drop_tree()

    candidates = doc.xpath("//article") or doc.xpath("//main")
    if candidates:
        container = max(candidates, key=lambda el: len(el.text_content()))
    else:
        # Fallback: the element (of any tag) with the most cumulative <p>
        # text is almost always the real article body, not the nav/sidebar.
        best_el, best_len = None, 0
        for el in doc.iter():
            p_text = "".join(p.text_content() for p in el.findall("p"))
            if len(p_text) > best_len:
                best_el, best_len = el, len(p_text)
        container = best_el if best_el is not None else doc

    paragraphs = [p.text_content().strip() for p in container.xpath(".//p")]
    paragraphs = [p for p in paragraphs if p]
    text = "\n\n".join(paragraphs) if paragraphs else container.text_content().strip()
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return title, text


def fetch_url_content(url: str) -> dict:
    """The one real entry point. Always returns a dict with "ok" -- never
    raises for a normal fetch/parse failure (those are honest ok=False
    results, not exceptions); only a genuinely unexpected error escapes.
    """
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "url": url, "reason": f"'{url}' is not a valid http(s) URL."}

    if _is_gated_domain(url):
        return {
            "ok": False,
            "url": url,
            "reason": (
                f"{_domain(url)} requires a logged-in session to view content and blocks "
                "anonymous fetches -- RBB has no authenticated access to it. Paste the text "
                "or a screenshot instead."
            ),
        }

    if httpx is None or lxml_html is None:
        return {"ok": False, "url": url, "reason": "URL fetching is not available on this server (missing httpx/lxml)."}

    try:
        resp = httpx.get(
            url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
    except httpx.TimeoutException:
        return {"ok": False, "url": url, "reason": f"The page took too long to respond (>{FETCH_TIMEOUT_SECONDS}s)."}
    except httpx.HTTPError as exc:
        return {"ok": False, "url": url, "reason": f"Couldn't reach that URL: {exc}"}

    if resp.status_code == 401 or resp.status_code == 403:
        return {"ok": False, "url": url, "reason": f"That page returned HTTP {resp.status_code} -- it likely requires login or blocks automated access."}
    if resp.status_code == 404:
        return {"ok": False, "url": url, "reason": "That page returned HTTP 404 -- the link may be broken or the content removed."}
    if resp.status_code >= 400:
        return {"ok": False, "url": url, "reason": f"That page returned HTTP {resp.status_code}."}

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type.lower():
        return {"ok": False, "url": url, "reason": f"That URL returned '{content_type or 'an unknown content type'}', not a webpage RBB can read as an article."}

    try:
        title, text = _extract_readable_text(resp.content, str(resp.url))
    except Exception as exc:  # noqa: BLE001 — a malformed/unusual page must not crash the tool call
        return {"ok": False, "url": url, "reason": f"Couldn't parse that page's content: {exc}"}

    if len(text) < MIN_READABLE_CHARS:
        return {
            "ok": False,
            "url": url,
            "reason": (
                "Fetched the page, but found too little readable article text to be useful "
                "(likely a login/consent wall, or a page that needs JavaScript to render). "
                "Paste the text or a screenshot instead."
            ),
        }

    return {
        "ok": True,
        "url": url,
        "final_url": str(resp.url),
        "title": title,
        "text": text,
        "word_count": len(text.split()),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 url_content_fetcher.py <url>", file=sys.stderr)
        return 2
    import json
    result = fetch_url_content(sys.argv[1])
    if result.get("ok") and len(result.get("text", "")) > 2000:
        result = dict(result, text=result["text"][:2000] + f"... [{result['word_count']} words total]")
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
