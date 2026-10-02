"""
test_html_entity_unescaping.py

Regression coverage: D+ Newsletter Inbox and "What Changed Today" both
rendered literal HTML entities from raw Gmail/newsletter HTML — "I&#39;m
working on..." instead of "I'm working on...", and LinkedIn newsletter URLs
kept "&amp;" in their query strings instead of "&". Title text was already
unescaped in _extract_body_articles; the href wasn't. Gmail subject/sender/
snippet fields were never unescaped at all before reaching rendered text.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import passive_email_intelligence as pei  # noqa: E402
import daily_brief as db  # noqa: E402
import rb_core as core  # noqa: E402


class TestExtractBodyArticlesUnescaping(unittest.TestCase):
    def test_url_query_string_ampersand_unescaped(self):
        html_body = (
            '<a href="https://www.linkedin.com/comm/pulse/example?lipi=abc&amp;'
            'midToken=xyz&amp;trk=eml-newsletter">A genuinely long article title here</a>'
        )
        articles = pei._extract_body_articles(html_body)
        self.assertEqual(len(articles), 1)
        self.assertIn("&midToken=xyz&trk=", articles[0]["url"])
        self.assertNotIn("&amp;", articles[0]["url"])

    def test_title_apostrophe_unescaped(self):
        html_body = (
            '<a href="https://example.com/a">This is a headline about America&#39;s future</a>'
        )
        articles = pei._extract_body_articles(html_body)
        self.assertEqual(len(articles), 1)
        self.assertIn("America's future", articles[0]["title"])
        self.assertNotIn("&#39;", articles[0]["title"])


class TestPersonalCorrespondenceUnescaping(unittest.TestCase):
    """The exact bug observed live: 'Good afternoon! I&#39;m working on
    finalizing the August schedule...' rendered with the literal entity."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.inbox_dir = Path(self.tmpdir.name)
        self._patch = patch.object(core, "INBOX_DIR", self.inbox_dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmpdir.cleanup()

    def test_snippet_and_subject_entities_unescaped(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        thread = {
            "subject": "August Tech &amp; Scheduling",
            "snippet": "Good afternoon! I&#39;m working on finalizing the August schedule.",
            "last_message_at": recent,
            "last_message_from": {"name": "Heather Jordan", "email": "heather@example.com"},
            "labels": ["CATEGORY_PERSONAL", "IMPORTANT", "INBOX"],
        }
        (self.inbox_dir / "email.personal.json").write_text(json.dumps({"threads": [thread]}))
        (self.inbox_dir / "email.bridgepoint.json").write_text(json.dumps({"threads": []}))

        items = db._compute_personal_intelligence_delta({}, {})
        correspondence = [i for i in items if (i.get("extras") or {}).get("delta_source") == "personal_correspondence"]
        self.assertEqual(len(correspondence), 1)
        item = correspondence[0]
        self.assertIn("I'm working", item["summary"])
        self.assertNotIn("&#39;", item["summary"])
        self.assertIn("August Tech & Scheduling", item["title"])
        self.assertNotIn("&amp;", item["title"])


if __name__ == "__main__":
    unittest.main()
