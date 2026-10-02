"""
test_url_content_fetcher.py

Regression coverage for url_content_fetcher.py, which backs rbb_chat.py's
fetchUrlContent tool (RB-2026-09-01). The core guarantee this enforces:
every failure mode returns a specific, honest reason -- never a silent
"success" with garbage, and the model is never left to fabricate content
for a URL RBB couldn't actually read (the same discipline as the
2026-08-27 ingestContent auto_persist incident, applied to a new surface).

Uses real httpx.Response objects (constructed directly, no network) as
fixtures rather than a generic mock -- this exercises the same attribute
access (.content, .headers, .status_code, .url) a real network response
would have, not a hand-rolled stand-in that might silently diverge from
httpx's real interface.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import url_content_fetcher as ucf  # noqa: E402


_ARTICLE_HTML = b"""
<html><head><title>A Real Test Article</title></head>
<body>
<nav><p>Home | About | Contact</p></nav>
<header><p>Site Header Junk</p></header>
<article>
<p>This is the first real paragraph of a genuine test article, long enough
to clear the minimum readable-content threshold this module enforces
before it will report a successful extraction back to the caller.</p>
<p>This is a second real paragraph, continuing the article with more
substantive content so the total extracted text is unambiguously a real
article body rather than a stray fragment of boilerplate navigation text
picked up by accident from somewhere else on the page.</p>
</article>
<footer><p>Copyright footer junk</p></footer>
</body></html>
"""

_THIN_SHELL_HTML = b"""
<html><head><title>JS App Shell</title></head>
<body><div id="root"><p>Loading...</p></div></body></html>
"""


def _response(url: str, *, status_code: int = 200, content: bytes = b"", content_type: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(
        status_code=status_code, content=content,
        headers={"content-type": content_type},
        request=httpx.Request("GET", url),
    )


class TestUrlValidation(unittest.TestCase):
    def test_rejects_non_http_scheme(self):
        result = ucf.fetch_url_content("ftp://example.com/file")
        self.assertFalse(result["ok"])
        self.assertIn("not a valid", result["reason"])

    def test_rejects_garbage_input(self):
        result = ucf.fetch_url_content("not a url at all")
        self.assertFalse(result["ok"])

    def test_rejects_empty_string(self):
        result = ucf.fetch_url_content("")
        self.assertFalse(result["ok"])


class TestGatedDomainShortCircuit(unittest.TestCase):
    def test_linkedin_short_circuits_without_a_real_http_call(self):
        with patch.object(ucf.httpx, "get") as mock_get:
            result = ucf.fetch_url_content("https://www.linkedin.com/pulse/some-real-looking-post-abc123")
        mock_get.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("logged-in session", result["reason"])

    def test_linkedin_bare_domain_also_matches(self):
        with patch.object(ucf.httpx, "get") as mock_get:
            result = ucf.fetch_url_content("https://linkedin.com/in/someone")
        mock_get.assert_not_called()
        self.assertFalse(result["ok"])


class TestRealFetchAndExtraction(unittest.TestCase):
    def test_clean_article_extracts_real_text(self):
        with patch.object(ucf.httpx, "get", return_value=_response("https://example.com/a", content=_ARTICLE_HTML)):
            result = ucf.fetch_url_content("https://example.com/a")
        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "A Real Test Article")
        self.assertIn("first real paragraph", result["text"])
        self.assertIn("second real paragraph", result["text"])
        # Boilerplate must not leak into the extracted article text.
        self.assertNotIn("Site Header Junk", result["text"])
        self.assertNotIn("Copyright footer junk", result["text"])
        self.assertNotIn("Home | About | Contact", result["text"])
        self.assertGreater(result["word_count"], 0)
        self.assertIn("fetched_at", result)

    def test_401_is_honest_failure(self):
        with patch.object(ucf.httpx, "get", return_value=_response("https://example.com/a", status_code=401)):
            result = ucf.fetch_url_content("https://example.com/a")
        self.assertFalse(result["ok"])
        self.assertIn("401", result["reason"])

    def test_403_is_honest_failure(self):
        with patch.object(ucf.httpx, "get", return_value=_response("https://example.com/a", status_code=403)):
            result = ucf.fetch_url_content("https://example.com/a")
        self.assertFalse(result["ok"])
        self.assertIn("403", result["reason"])

    def test_404_is_honest_failure(self):
        with patch.object(ucf.httpx, "get", return_value=_response("https://example.com/a", status_code=404)):
            result = ucf.fetch_url_content("https://example.com/a")
        self.assertFalse(result["ok"])
        self.assertIn("404", result["reason"])

    def test_non_html_content_type_is_honest_failure(self):
        with patch.object(ucf.httpx, "get", return_value=_response(
            "https://example.com/a.pdf", content=b"%PDF-1.4", content_type="application/pdf",
        )):
            result = ucf.fetch_url_content("https://example.com/a.pdf")
        self.assertFalse(result["ok"])
        self.assertIn("application/pdf", result["reason"])

    def test_thin_js_shell_reports_failure_not_false_success(self):
        """The real bug class this guards against: a page that 'succeeds'
        at the HTTP level but has nothing real to extract (JS-rendered
        shell, consent wall) must not be reported as a successful fetch
        with near-empty text -- that would let the model treat 'Loading...'
        as if it were real article content."""
        with patch.object(ucf.httpx, "get", return_value=_response("https://example.com/a", content=_THIN_SHELL_HTML)):
            result = ucf.fetch_url_content("https://example.com/a")
        self.assertFalse(result["ok"])
        self.assertIn("too little readable", result["reason"])

    def test_timeout_is_honest_failure(self):
        with patch.object(ucf.httpx, "get", side_effect=httpx.TimeoutException("timed out")):
            result = ucf.fetch_url_content("https://example.com/a")
        self.assertFalse(result["ok"])
        self.assertIn("too long", result["reason"])

    def test_connection_error_is_honest_failure(self):
        with patch.object(ucf.httpx, "get", side_effect=httpx.ConnectError("connection refused")):
            result = ucf.fetch_url_content("https://example.com/a")
        self.assertFalse(result["ok"])
        self.assertIn("Couldn't reach", result["reason"])

    def test_result_never_raises_on_malformed_html(self):
        with patch.object(ucf.httpx, "get", return_value=_response("https://example.com/a", content=b"<<<not even close to html")):
            result = ucf.fetch_url_content("https://example.com/a")
        # Either a graceful ok=False or a best-effort ok=True -- the
        # contract is "never raises," not a specific outcome for garbage input.
        self.assertIn("ok", result)


if __name__ == "__main__":
    unittest.main()
