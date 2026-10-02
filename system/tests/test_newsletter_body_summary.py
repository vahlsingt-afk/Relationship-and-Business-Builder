"""
test_newsletter_body_summary.py

Regression coverage: D+ Newsletter Inbox showed Michael "schatzy" Schatzberg's
LinkedIn newsletter as a bare list of link titles that were actually LinkedIn
chrome, not article content: "Hospitality Headline" (the newsletter's own
masthead, self-linked), "Michael 'schatzy' Schatzberg" (the author's profile
link), "CONTINUE READING HERE" / "Learn why we included this." (CTA button
labels with no content of their own). None of this told Todd what the
newsletter actually said — per his feedback, D+ needs "more of a summary"
for personal LinkedIn-style newsletters, comparable to what Section C
already provides per-article for industry sources like Restaurant Dive.

Root cause: _extract_body_articles only ever captured <a> anchor text — for
LinkedIn's template, the real content (the author's lead paragraph) sits as
plain text next to the links, not inside them, and nothing extracted it.

Fix:
- _extract_newsletter_summary() (passive_email_intelligence.py) strips the
  LinkedIn masthead/footer boilerplate and HTML noise, returning the lead
  paragraph truncated to a sentence boundary.
- _extract_body_articles now also skips linkedin.com/comm/newsletters/... and
  linkedin.com/comm/in/... URLs (the masthead self-link and author-profile
  link, identified by URL pattern rather than title text since the anchor
  text is just the publication/author name and no text heuristic reliably
  tells those apart from a real headline) and shout-caps CTA labels
  ("CONTINUE READING HERE", "LISTEN NOW", "FIND OUT HERE").
- render_intelligence_brief.py's _render_newsletter_inbox renders the body
  summary (extras.body_summary) under the source header when present.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import passive_email_intelligence as pei  # noqa: E402
import render_intelligence_brief as rib  # noqa: E402


_LINKEDIN_NEWSLETTER_HTML = """
<html><body>
<div style="display:none">Skin in the Game</div>
<a href="https://www.linkedin.com/comm/newsletters/12345?trk=x">Hospitality Headline</a>
This Hospitality Headline, a newsletter about Foodservice, Hospitality, Technology & Investments
<a href="https://www.linkedin.com/comm/pulse/skin-game-abc?trk=y">Skin in the Game</a>
<a href="https://www.linkedin.com/comm/in/schatzy007?trk=z">Michael 'schatzy' Schatzberg</a>
<a href="https://www.linkedin.com/comm/pulse/skin-game-abc2?trk=y2">Read this article on LinkedIn to join the conversation</a>
Read on LinkedIn
Commitment vs Involvement Friends of Branded! Happy Saturday, and I hope you had a great week.
I've spent the better part of three decades watching capital and culture try to shake hands.
Most of the time it's just a photo-op, but every so often you see a real partnership come
together, and this summer handed us both in the same news cycle.
<a href="https://www.linkedin.com/comm/pulse/continue-reading?trk=w">CONTINUE READING HERE</a>
<a href="https://www.linkedin.com/comm/pulse/donatos-episode?trk=v">Kevin King of Donatos Pizza talks pizza history and brand growth</a>
<a href="https://www.linkedin.com/comm/pulse/find-b-list?trk=u">FIND OUT HERE</a>
This email was intended for Todd Vahlsing (Restaurant Operations Executive)
<a href="https://www.linkedin.com/help/linkedin/answer/4788">Learn why we included this.</a>
You are receiving LinkedIn notification emails. Unsubscribe · Help
</body></html>
"""


class TestExtractNewsletterSummary(unittest.TestCase):
    def test_returns_lead_paragraph_not_masthead_or_footer(self):
        summary = pei._extract_newsletter_summary(_LINKEDIN_NEWSLETTER_HTML)
        self.assertIn("Commitment vs Involvement", summary)
        self.assertIn("three decades watching capital and culture", summary)
        self.assertNotIn("This email was intended for", summary)
        self.assertNotIn("Hospitality Headline, a newsletter about", summary)
        self.assertNotIn("Unsubscribe", summary)

    def test_empty_body_returns_empty_summary(self):
        self.assertEqual(pei._extract_newsletter_summary(""), "")

    def test_falls_back_gracefully_without_linkedin_anchor(self):
        html = "<html><body>Just a plain article intro paragraph with real content here.</body></html>"
        summary = pei._extract_newsletter_summary(html)
        self.assertIn("plain article intro paragraph", summary)


_MULTI_ARTICLE_ROUNDUP_HTML = """
<html><body>
<div style="font-size:1px;line-height:10px">&nbsp;</div>
<p><strong><span style="font-size:24px">
<a href="http://click1.mail.qsrmagazine.com/track1.html?a=1&b=2">What Gen Z Really Wants From Caffeinated Beverages</a>
</span></strong></p>
<p><span>Meet the demand for ingredient transparency with fast-executing, high-performance beverage formats. <i>Partner content with Sunny Sky Products.</i></span></p>
<table class="divider"><tbody><tr><td>&nbsp;</td></tr></tbody></table>
<p><strong><span style="font-size:20px">
<a href="http://click1.mail.qsrmagazine.com/track2.html?a=1&b=2">How a High Volume Jersey Mike's Turns Cleanliness Into a Competitive Advantage</a>
</span></strong></p>
<p><span>Consistent cleaning practices help reinforce guest trust and repeat visits.</span></p>
</body></html>
"""


class TestMultiArticleRoundupSummaryStopsAtFirstArticle(unittest.TestCase):
    """RB-DEFECT-2026-07-10f: multi-article newsletter roundups (QSR AM
    Jolt) put each article's headline+blurb in its own <p> pair with no
    separator text before the next article's headline+blurb -- tag-stripping
    alone collapsed the block boundary, so the first article's summary ran
    straight into the second article's headline and blurb with no
    indication they were two unrelated stories."""

    def test_summary_excludes_second_article_headline_and_blurb(self):
        summary = pei._extract_newsletter_summary(_MULTI_ARTICLE_ROUNDUP_HTML)
        self.assertIn("What Gen Z Really Wants From Caffeinated Beverages", summary)
        self.assertIn("Partner content with Sunny Sky Products", summary)
        self.assertNotIn("Jersey Mike", summary)
        self.assertNotIn("Consistent cleaning practices", summary)

    def test_single_article_body_unaffected(self):
        html = "<html><body><p>Just a plain article intro paragraph with real content here.</p></body></html>"
        summary = pei._extract_newsletter_summary(html)
        self.assertIn("plain article intro paragraph", summary)


class TestHiddenPreheaderDuplicationStripped(unittest.TestCase):
    """RB-DEFECT-2026-07-10: many marketing-newsletter templates (e.g. QSR
    AM Jolt) include a hidden "preheader" element purely to control the
    inbox preview snippet -- invisible in a rendered client, but plain text
    once tags are stripped, and it typically repeats the newsletter's own
    opening line verbatim. Observed live: "96 A.M. Jolt - QSR Magazine And
    Chipotle makes six new food tech investments. And Chipotle makes six
    new food tech investments. If you are having trouble reading this
    email, you may view the online version." -- the sentence duplicated
    back-to-back, plus "view the online version" boilerplate leaking
    through as if it were content."""

    def test_display_none_preheader_duplication_removed(self):
        html = (
            '<div style="display:none; max-height:0px; overflow:hidden;">'
            "And Chipotle makes six new food tech investments.</div>"
            "<body>96 A.M. Jolt - QSR Magazine. And Chipotle makes six new food tech investments. "
            "If you are having trouble reading this email, you may view the online version.</body>"
        )
        summary = pei._extract_newsletter_summary(html)
        self.assertEqual(summary.count("Chipotle makes six new food tech investments"), 1)
        self.assertNotIn("trouble reading this email", summary)
        self.assertNotIn("online version", summary)

    def test_immediate_sentence_repeat_collapsed_without_hidden_div(self):
        """Defensive pass for templates that duplicate the preview line by
        some other means than a recognizable display:none/mso-hide wrapper."""
        text = "Chipotle makes six new food tech investments. Chipotle makes six new food tech investments. More real content follows."
        collapsed = pei._collapse_immediate_sentence_repeat(text)
        self.assertEqual(collapsed.count("Chipotle makes six new food tech investments"), 1)
        self.assertIn("More real content follows", collapsed)


class TestExtractBodyArticlesDropsLinkedInChrome(unittest.TestCase):
    def test_masthead_and_author_profile_links_excluded(self):
        articles = pei._extract_body_articles(_LINKEDIN_NEWSLETTER_HTML)
        titles = [a["title"] for a in articles]
        self.assertNotIn("Hospitality Headline", titles)
        self.assertNotIn("Michael 'schatzy' Schatzberg", titles)

    def test_shout_caps_cta_labels_excluded(self):
        articles = pei._extract_body_articles(_LINKEDIN_NEWSLETTER_HTML)
        titles = [a["title"] for a in articles]
        self.assertNotIn("CONTINUE READING HERE", titles)
        self.assertNotIn("FIND OUT HERE", titles)

    def test_real_article_title_still_captured(self):
        articles = pei._extract_body_articles(_LINKEDIN_NEWSLETTER_HTML)
        titles = [a["title"] for a in articles]
        self.assertTrue(any("Donatos Pizza" in t for t in titles))


class TestRenderNewsletterInboxShowsBodySummary(unittest.TestCase):
    def test_body_summary_rendered_under_header(self):
        sections = {
            "newsletter_intelligence": [{
                "title": "Michael 'schatzy' Schatzberg via LinkedIn",
                "extras": {
                    "source_name": "Michael 'schatzy' Schatzberg via LinkedIn",
                    "pub_date": "Jul 04, 2026",
                    "body_summary": "Commitment vs Involvement — a real lead paragraph about restaurant M&A.",
                    "articles": [
                        {"title": "Kevin King of Donatos Pizza talks pizza history and brand growth",
                         "url": "https://example.com/donatos"},
                    ],
                },
            }]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertIn("Commitment vs Involvement", out)
        # RB-2026-08-28: RB-DEFECT-2026-08-14 added MIN_ARTICLES_PER_NEWSLETTER=3
        # (skip a roundup-style article list under that floor, to avoid a
        # newsletter rendering with one lonely link). Its first version
        # dropped the WHOLE edition in that case -- a real bug found here:
        # an edition with both a body_summary and 1-2 sub-floor articles lost
        # the summary too, even though the summary has nothing to do with
        # why the article list didn't clear the bar. Fixed in
        # render_intelligence_brief.py to drop only the under-floor article
        # list, not the summary. This is now the real, intended behavior:
        # the summary renders; a single article can't stand alone as a list.
        self.assertNotIn("Donatos Pizza", out)


class TestEssayFormatNewsletterWithNoArticleLinksStillRenders(unittest.TestCase):
    """Regression: after filtering the masthead/author-profile/CTA junk from
    Schatzy's LinkedIn newsletter, its article list can end up completely
    empty (an essay-format edition with no real article links, just the
    author's own write-up) — daily_brief.py's `if not articles: continue`
    and render_intelligence_brief.py's `if not valid: continue` both dropped
    the WHOLE edition in that case, discarding the one thing (body_summary)
    that made it worth reading. Both must fall through to body_summary before
    giving up on the edition."""

    def test_render_keeps_edition_with_summary_but_zero_articles(self):
        sections = {
            "newsletter_intelligence": [{
                "title": "Michael 'schatzy' Schatzberg via LinkedIn",
                "extras": {
                    "source_name": "Michael 'schatzy' Schatzberg via LinkedIn",
                    "pub_date": "Jul 04, 2026",
                    "body_summary": "Commitment vs Involvement — a real essay about restaurant industry M&A trends.",
                    "articles": [],
                },
            }]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertIn("Michael 'schatzy' Schatzberg via LinkedIn", out)
        self.assertIn("Commitment vs Involvement", out)

    def test_render_drops_edition_with_neither_articles_nor_summary(self):
        sections = {
            "newsletter_intelligence": [{
                "title": "Empty Newsletter",
                "extras": {
                    "source_name": "Empty Newsletter",
                    "pub_date": "Jul 04, 2026",
                    "body_summary": "",
                    "articles": [],
                },
            }]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()


class TestSchatzbergNewsletterAlwaysIncluded(unittest.TestCase):
    """Regression: Schatzy's "Hospitality Headline" newsletter lost its D+
    slot entirely once the masthead self-link ("Hospitality Headline" —
    the one anchor that happened to carry the "hospitality" keyword) was
    correctly filtered out as junk chrome, and the extracted body_summary
    is often a personal-interest teaser (sports, holidays) before the real
    content, so neither article titles nor the summary reliably contain an
    industry keyword. This is a deliberately-kept, wanted publication (per
    the fetch-domain broadening done specifically for it) — it should never
    lose a D+ slot to a generic recruiter/lifestyle newsletter for lacking
    a keyword match its own content structure doesn't reliably produce."""

    def test_schatzberg_source_is_known_industry(self):
        self.assertTrue(any("schatzberg" in p for p in rib._PRIORITY_NEWSLETTERS))

    def test_renders_even_with_off_topic_summary_and_no_keyword_articles(self):
        sections = {
            "newsletter_intelligence": [{
                "title": "Michael 'schatzy' Schatzberg via LinkedIn",
                "extras": {
                    "source_name": "Michael 'schatzy' Schatzberg via LinkedIn",
                    "pub_date": "Jul 04, 2026",
                    "body_summary": "Happy Saturday! I'm a lifelong New York Knicks fan, still smiling about the title.",
                    "articles": [],
                },
            }]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertIn("Michael 'schatzy' Schatzberg via LinkedIn", out)
