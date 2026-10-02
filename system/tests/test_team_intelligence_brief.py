"""
test_team_intelligence_brief.py

Regression coverage: Todd asked for a version of the Intelligence Brief that
strips his own personal information -- a general industry brief he might
choose to forward to his team, not a document scoped to his employer.
render_intelligence_brief.render_team_edition reuses the same section
renderers as the personal brief for pure-news sections (A-E, D+, F, I) but
drops every section that's about Todd himself (What Changed Today, G:
Opportunities, H: Relationship Deltas, Identity Match Candidates, Capture
Intelligence, J: Proof Dashboard), drops K: GP/Genius Field Intelligence
entirely (it exists only to track his employer and its product), and
filters every remaining section to exclude any item mentioning "Global
Payments" or "Genius" by name. Escalated watchlist entries are also
excluded from F since that status reflects Todd's own overdue-loop
follow-up tracking, not newsworthy entity activity.

Also covers a real data-quality finding hit while building this: Restaurant
Dive's tradepub.com "partner content" links embed the subscriber's email,
name, and employer directly in a base64-encoded path segment of the URL --
visible just by looking at the link, no click required. _strip_pii_links
scans every markdown link in the assembled team edition (raw URL and any
base64-decodable path segment) and drops the hyperlink (keeping the label)
if it matches Todd's own identifying info.
"""
from __future__ import annotations

import base64
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _b64(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


class TestUrlLeaksPii(unittest.TestCase):
    def test_plaintext_email_in_url_detected(self):
        self.assertTrue(rib._url_leaks_pii(
            "https://example.com/track?email=vahlsingt@gmail.com&first=Todd"))

    def test_base64_encoded_pii_in_path_segment_detected(self):
        encoded = _b64("https://restaurantdive.tradepub.com/c/pubRD.mpl?"
                        "email=vahlsingt@gmail.com&first=Todd&last=Vahlsing&company=BridgepointOps")
        url = f"https://link.restaurantdive.com/click/123.456/{encoded}/deadbeef"
        self.assertTrue(rib._url_leaks_pii(url))

    def test_clean_url_not_flagged(self):
        encoded = _b64("https://restaurantdive.tradepub.com/c/pubRD.mpl?ch=Library&utm_source=RSTD")
        url = f"https://link.restaurantdive.com/click/123.456/{encoded}/deadbeef"
        self.assertFalse(rib._url_leaks_pii(url))
        self.assertFalse(rib._url_leaks_pii("https://www.restaurantdive.com/news/some-story/824612/"))


class TestStripPiiLinks(unittest.TestCase):
    def test_leaking_link_delinked_but_label_kept(self):
        encoded = _b64("https://restaurantdive.tradepub.com/c/pubRD.mpl?"
                        "email=vahlsingt@gmail.com&first=Todd&last=Vahlsing")
        url = f"https://link.restaurantdive.com/click/123.456/{encoded}/x"
        md = f"- [Use BOH Tech to Drive Restaurant Savings]({url})"
        out = rib._strip_pii_links(md)
        self.assertEqual(out, "- Use BOH Tech to Drive Restaurant Savings")

    def test_clean_link_untouched(self):
        md = "- [Dairy Queen hires Shake Shack vet](https://www.restaurantdive.com/news/x/824612/)"
        out = rib._strip_pii_links(md)
        self.assertEqual(out, md)


def _fake_cache(sections: dict) -> dict:
    return {
        "_generated_at": "2026-07-10T15:50:00+00:00",
        "data": {"canonical_brief": {"sections": sections}},
    }


class TestRenderTeamEditionOmitsPersonalSections(unittest.TestCase):
    def _render(self, tmp_path: Path, sections: dict) -> str:
        cache_path = tmp_path / "daily_brief.json"
        cache_path.write_text(json.dumps(_fake_cache(sections)), encoding="utf-8")
        with patch.object(rib, "DAILY_BRIEF_CACHE", cache_path), \
             patch.object(rib, "BRIEFS_DIR", tmp_path):
            return rib.render_team_edition(date(2026, 7, 10), dry_run=True)

    def test_no_data_returns_placeholder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), {})
        self.assertIn("No data available", out)

    def test_news_sections_included(self):
        import tempfile
        sections = {
            "world_national_headlines": [
                {"title": "Some world event happens", "summary": "Detail.",
                 "extras": {"source_url": "https://bbc.co.uk/news/1", "source_name": "BBC",
                            "pub_date": "2026-07-10", "scope": "world"}},
            ],
            "restaurant_industry_headlines": [
                {"title": "Chipotle launches a restaurant traffic promotion", "summary": "Detail.",
                 "extras": {"source_url": "https://nrn.com/news/1", "source_name": "NRN",
                            "pub_date": "2026-07-10"}},
            ],
            "watchlist_intelligence": [
                {"title": "Chipotle — No Change", "extras": {"entity_name": "Chipotle",
                 "watchlist_status": "No Change"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertIn("Restaurant & Payments Industry Brief", out)
        self.assertIn("A: Restaurant Industry", out)

    def test_world_and_national_sections_never_render(self):
        """Out of scope for a restaurant-industry digest -- world/national
        headline data must never surface as its own section, even when present."""
        import tempfile
        sections = {
            "world_national_headlines": [
                {"title": "Some world event happens", "summary": "Detail.",
                 "extras": {"source_url": "https://bbc.co.uk/news/1", "source_name": "BBC",
                            "pub_date": "2026-07-10", "scope": "world"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("World Headlines", out)
        self.assertNotIn("National Headlines", out)

    def test_escalated_watchlist_entity_excluded_but_new_activity_kept(self):
        import tempfile
        sections = {
            "watchlist_intelligence": [
                {"title": "McDonald's — Escalation", "why_it_matters": "Overdue loop.",
                 "recommended_action": "Follow up immediately.",
                 "extras": {"entity_name": "McDonald's", "watchlist_status": "Escalation"}},
                {"title": "Domino's — New Activity", "why_it_matters": "Domino's press release: Q2 expansion.",
                 "extras": {"entity_name": "Domino's", "watchlist_status": "New Activity",
                            "source_url": "https://prnewswire.com/dominos-q2"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("ESCALATED", out)
        self.assertNotIn("Overdue loop", out)
        self.assertIn("Domino's", out)

    def test_personal_only_sections_never_render(self):
        import tempfile
        sections = {
            "world_national_headlines": [
                {"title": "Some world event happens", "summary": "Detail.",
                 "extras": {"source_url": "https://bbc.co.uk/news/1", "source_name": "BBC",
                            "pub_date": "2026-07-10", "scope": "world"}},
            ],
            "opportunity_signals": [{"title": "Todd's own job opportunity"}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("G: Opportunities", out)
        self.assertNotIn("H: Relationship Deltas", out)
        self.assertNotIn("What Changed Today", out)
        self.assertNotIn("Proof Dashboard", out)
        self.assertNotIn("Capture Intelligence", out)


class TestRenderTeamEditionRemovesGeniusAndGlobalPaymentsMentions(unittest.TestCase):
    """This is Todd's own general industry brief, not a document scoped to
    his employer -- he decides case by case whether it's worth forwarding to
    his team. Section K: GP/Genius Field Intelligence exists solely to track
    his employer (Global Payments) and its product (Genius), so it's dropped
    entirely; every remaining section filters out any item that names either
    one, and the title/footer no longer brand the document as Genius's."""

    def _render(self, tmp_path: Path, sections: dict) -> str:
        cache_path = tmp_path / "daily_brief.json"
        cache_path.write_text(json.dumps(_fake_cache(sections)), encoding="utf-8")
        with patch.object(rib, "DAILY_BRIEF_CACHE", cache_path), \
             patch.object(rib, "BRIEFS_DIR", tmp_path):
            return rib.render_team_edition(date(2026, 7, 10), dry_run=True)

    def test_section_k_never_renders_even_with_gp_relevant_content(self):
        import tempfile
        sections = {
            "restaurant_industry_headlines": [
                {"title": "Global Payments reports quarterly earnings", "summary": "Detail.",
                 "extras": {"source_url": "https://example.com/gp-earnings", "source_name": "Reuters Business",
                            "pub_date": "2026-07-10"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("GP/Genius", out)
        self.assertNotIn("Field Intelligence", out)

    def test_title_and_footer_do_not_brand_as_genius(self):
        with_tempdir = __import__("tempfile").TemporaryDirectory()
        with with_tempdir as tmpdir:
            out = self._render(Path(tmpdir), {})
        self.assertNotIn("Genius", out)
        self.assertIn("Restaurant & Payments Industry Brief", out)

    def test_headline_mentioning_global_payments_by_name_excluded(self):
        import tempfile
        sections = {
            "restaurant_industry_headlines": [
                {"title": "Global Payments reports quarterly earnings", "summary": "Beat expectations.",
                 "extras": {"source_url": "https://example.com/gp-earnings", "source_name": "Reuters",
                            "pub_date": "2026-07-10"}},
                {"title": "Toast rolls out new loyalty platform for franchise operators", "summary": "Detail.",
                 "extras": {"source_url": "https://example.com/other", "source_name": "NRN",
                            "pub_date": "2026-07-10"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("Global Payments reports quarterly earnings", out)
        self.assertIn("Toast rolls out new loyalty platform for franchise operators", out)

    def test_watchlist_entity_named_genius_or_global_payments_excluded(self):
        import tempfile
        sections = {
            "watchlist_intelligence": [
                {"title": "Genius — New Activity", "why_it_matters": "Product launch.",
                 "extras": {"entity_name": "Genius", "watchlist_status": "New Activity",
                            "source_url": "https://example.com/genius-launch"}},
                {"title": "Global Payments — Relevant Activity",
                 "extras": {"entity_name": "Global Payments", "watchlist_status": "Relevant Activity"}},
                {"title": "Domino's — New Activity", "why_it_matters": "Domino's store expansion.",
                 "extras": {"entity_name": "Domino's", "watchlist_status": "New Activity",
                            "source_url": "https://example.com/dominos"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("Genius", out)
        self.assertNotIn("Global Payments", out)
        self.assertIn("Domino's", out)

    def test_strategic_signal_mentioning_banned_terms_excluded(self):
        import tempfile
        sections = {
            "strategic_industry_signals": [
                {"title": "Multiple-source convergence: Global Payments / Genius AI integration",
                 "summary": "across 10 evidence item(s) and 3 source channel(s)."},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("Global Payments", out)
        self.assertNotIn("Genius", out)

    def test_newsletter_article_mentioning_banned_terms_excluded(self):
        import tempfile
        sections = {
            "newsletter_intelligence": [{
                "title": "Payments Dive",
                "extras": {
                    "source_name": "Payments Dive",
                    "pub_date": "Jul 08, 2026",
                    "body_summary": "Today's top payments stories.",
                    "articles": [
                        {"title": "Global Payments announces new POS integration",
                         "url": "https://example.com/gp-pos"},
                        {"title": "Western Union navigates digital shift",
                         "url": "https://example.com/other-payments"},
                    ],
                },
            }],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("Global Payments announces new POS integration", out)
        self.assertIn("Western Union navigates digital shift", out)


class TestSinceWindowHelper(unittest.TestCase):
    def test_none_window_passes_everything_through(self):
        items = [{"extras": {"pub_date": "2026-06-01"}}, {"extras": {}}]
        self.assertEqual(rib._since_window(items, None), items)

    def test_item_before_window_start_excluded(self):
        items = [
            {"title": "old", "extras": {"pub_date": "2026-07-01"}},
            {"title": "new", "extras": {"pub_date": "2026-07-09"}},
        ]
        out = rib._since_window(items, date(2026, 7, 8))
        self.assertEqual([i["title"] for i in out], ["new"])

    def test_item_with_no_pub_date_always_kept(self):
        items = [{"title": "undated", "extras": {}}]
        out = rib._since_window(items, date(2026, 7, 8))
        self.assertEqual(len(out), 1)


class TestLastTeamBriefDate(unittest.TestCase):
    def test_no_prior_editions_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(rib, "BRIEFS_DIR", Path(tmpdir)):
                self.assertIsNone(rib._last_team_brief_date(date(2026, 7, 10)))

    def test_finds_most_recent_prior_edition(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "2026-07-03-team-intelligence-brief.md").write_text("x", encoding="utf-8")
            (tmp / "2026-07-07-team-intelligence-brief.md").write_text("x", encoding="utf-8")
            with patch.object(rib, "BRIEFS_DIR", tmp):
                found = rib._last_team_brief_date(date(2026, 7, 10))
        self.assertEqual(found, date(2026, 7, 7))

    def test_ignores_editions_on_or_after_target(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "2026-07-10-team-intelligence-brief.md").write_text("x", encoding="utf-8")
            with patch.object(rib, "BRIEFS_DIR", tmp):
                found = rib._last_team_brief_date(date(2026, 7, 10))
        self.assertIsNone(found)

    def test_ignores_non_publish_weekday_leftover_files(self):
        """RB-DEFECT-2026-08-11 regression: before the Tue/Fri-only cadence,
        this pipeline rendered a file every day, so BRIEFS_DIR is full of
        old-regime files dated on ordinary weekdays. A Monday leftover must
        not be mistaken for the last real edition -- only the true prior
        Tuesday/Friday counts, even if an off-weekday file is more recent."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            # 2026-08-07 is a Friday (real prior edition, old regime or new).
            (tmp / "2026-08-07-team-intelligence-brief.md").write_text("x", encoding="utf-8")
            # 2026-08-10 is a Monday (old daily-cadence leftover, not a real
            # publish under the new Tue/Fri-only rule).
            (tmp / "2026-08-10-team-intelligence-brief.md").write_text("x", encoding="utf-8")
            with patch.object(rib, "BRIEFS_DIR", tmp):
                # 2026-08-11 is a Tuesday.
                found = rib._last_team_brief_date(date(2026, 8, 11))
        self.assertEqual(found, date(2026, 8, 7))


class TestRenderTeamEditionAccumulatesSinceLastPublished(unittest.TestCase):
    """A Tuesday edition (window_start = last Friday + 1) must include a
    Saturday/Sunday/Monday story and exclude one from before the prior
    (Friday) edition -- the whole point of accumulating since the last
    published date instead of rendering a fixed daily cut."""

    def _render_with_prior_edition(self, tmp_path: Path, prior_date: date,
                                    target_date: date, sections: dict) -> str:
        cache_path = tmp_path / "daily_brief.json"
        cache_path.write_text(json.dumps(_fake_cache(sections)), encoding="utf-8")
        (tmp_path / f"{prior_date.isoformat()}-team-intelligence-brief.md").write_text(
            "# prior edition", encoding="utf-8")
        with patch.object(rib, "DAILY_BRIEF_CACHE", cache_path), \
             patch.object(rib, "BRIEFS_DIR", tmp_path):
            return rib.render_team_edition(target_date, dry_run=True)

    def test_story_before_last_published_date_excluded(self):
        import tempfile
        # Prior edition published Friday 2026-07-10. This Tuesday's (07-14)
        # window starts 07-11. A story from 07-09 (before Friday) must not
        # reappear.
        sections = {
            "restaurant_industry_headlines": [
                {"title": "Toast POS update — stale pre-Friday story", "summary": "Detail.",
                 "extras": {"source_url": "https://nrn.com/old", "source_name": "NRN",
                            "pub_date": "2026-07-09"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render_with_prior_edition(
                Path(tmpdir), date(2026, 7, 10), date(2026, 7, 14), sections)
        self.assertNotIn("stale pre-Friday story", out)

    def test_story_since_last_published_date_included(self):
        import tempfile
        sections = {
            "restaurant_industry_headlines": [
                {"title": "Toast POS update — weekend story after Friday edition", "summary": "Detail.",
                 "extras": {"source_url": "https://nrn.com/new", "source_name": "NRN",
                            "pub_date": "2026-07-12"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render_with_prior_edition(
                Path(tmpdir), date(2026, 7, 10), date(2026, 7, 14), sections)
        self.assertIn("weekend story after Friday edition", out)


class TestTeamExecutiveEditorialStandard(unittest.TestCase):
    def _render(self, tmp_path: Path, sections: dict) -> str:
        cache_path = tmp_path / "daily_brief.json"
        cache_path.write_text(json.dumps(_fake_cache(sections)), encoding="utf-8")
        with patch.object(rib, "DAILY_BRIEF_CACHE", cache_path), \
             patch.object(rib, "BRIEFS_DIR", tmp_path):
            return rib.render_team_edition(date(2026, 8, 18), dry_run=True)

    def test_ai_platform_is_classified_as_restaurant_technology(self):
        import tempfile
        sections = {"restaurant_industry_headlines": [{
            "title": "Palona AI unveils operating layer for restaurants",
            "summary": "A multimodal AI platform for physical businesses.",
            "extras": {"source_url": "https://example.com/palona", "source_name": "QSR",
                       "pub_date": "2026-08-17"},
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertIn("B: Restaurant Technology", out)
        self.assertNotIn("## A: Restaurant Industry", out)

    def test_newsletter_lead_is_linked_and_raw_preview_is_not_rendered(self):
        import tempfile
        sections = {"newsletter_intelligence": [{
            "title": "Restaurant Dive",
            "extras": {
                "source_name": "Restaurant Dive", "pub_date": "Aug 17, 2026",
                "body_summary": "View online | Signup Daily Dive BROUGHT TO YOU BY sponsor copy",
                "articles": [{"title": "Pizza Hut’s Global CEO resigns",
                              "url": "https://example.com/pizza-hut-ceo"}],
            },
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertIn("[Pizza Hut’s Global CEO resigns](https://example.com/pizza-hut-ceo)", out)
        self.assertIn("Why it matters:", out)
        self.assertNotIn("Signup Daily Dive", out)
        self.assertNotIn("BROUGHT TO YOU BY", out)

    def test_single_strong_newsletter_story_does_not_need_filler(self):
        import tempfile
        sections = {"newsletter_intelligence": [{
            "title": "Payments Dive",
            "extras": {"source_name": "Payments Dive", "pub_date": "Aug 17, 2026",
                       "articles": [{"title": "Western Union navigates digital shift",
                                     "url": "https://example.com/wu"}]},
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertIn("Western Union", out)
        self.assertEqual(out.count("Why it matters:"), 1)

    def test_internal_earnings_api_instruction_never_reaches_team_report(self):
        import tempfile
        sections = {"watchlist_intelligence": [{
            "title": "Olo — Escalation [EARNINGS] 8-K - Current report",
            "why_it_matters": "Confirmed earnings release; no press-release text was retrievable (exhibit fetch failed).",
            "extras": {"entity_name": "Olo", "watchlist_status": "New Activity",
                       "category": "restaurant_tech_digital", "history_api": "getCompanyEarningsHistory",
                       "history_count": 1, "source_url": "https://sec.gov/olo"},
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("getCompanyEarningsHistory", out)
        self.assertNotIn("exhibit fetch failed", out)

    def test_decorative_masthead_title_rejected_real_headline_kept(self):
        """RB-2026-08-25: live in the 2026-08-25 Intelligence Brief --
        "::The latest in pizza news & marketing::" (a newsletter section
        masthead/wrapper, not a real article) rendered as a D+ item with a
        generic "why it matters." A real headline never opens and closes
        with the same run of decorative punctuation."""
        import tempfile
        sections = {"newsletter_intelligence": [{
            "title": "PMQ Pizza",
            "extras": {
                "source_name": "PMQ Pizza", "pub_date": "Aug 24, 2026",
                "articles": [
                    {"title": "::The latest in pizza news & marketing::",
                     "url": "https://example.com/pmq-masthead"},
                    {"title": "Domino's tests new loyalty tier for frequent diners",
                     "url": "https://example.com/dominos-loyalty"},
                ],
            },
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("pmq-masthead", out)
        self.assertNotIn("The latest in pizza news", out)
        self.assertIn("dominos-loyalty", out)


class TestTeamSynthesizeBatchTruncation(unittest.TestCase):
    """RB-2026-08-25: live in the 2026-08-25 Intelligence Brief -- D+'s
    "Why it matters" for a Payments Dive story cut off mid-word ("...crucial
    for sales professionals to understand these innovat"). _team_synthesize_
    batch's cache-write used a bare why[:280] slice; it must truncate on a
    word boundary like every other length-capped field in this renderer."""

    def test_long_synthesized_why_truncated_on_word_boundary_not_mid_word(self):
        long_why = (
            "The focus on AI-driven payment experiences highlights a growing "
            "trend in the restaurant technology sector, where integrating "
            "advanced payment solutions can enhance customer engagement and "
            "streamline operations, making it crucial for sales professionals "
            "to understand these innovations and their competitive implications."
        )
        self.assertGreater(len(long_why), 280)  # confirms this exercises truncation at all
        fake_result = {rib._team_synth_key("Agentic Commerce"): {"why": long_why}}
        with patch("brief_synthesis.synthesize_signals", return_value=fake_result), \
             patch.object(rib, "TEAM_SYNTH_CACHE_PATH", Path("/tmp/does-not-exist-synth-cache.json")), \
             patch.object(rib, "_save_json"):
            rib._team_synth_why = {}
            rib._team_synthesize_batch([{"title": "Agentic Commerce", "evidence": long_why}])
        cached = rib._team_synth_why[rib._team_synth_key("Agentic Commerce")]
        self.assertLessEqual(len(cached), 281)
        self.assertTrue(cached.endswith("…"))
        # Word-boundary truncation never leaves a fragment of the next real
        # word dangling right before the ellipsis.
        self.assertNotIn("innovat…", cached)


class TestWatchlistEvidenceNamesOwnEntity(unittest.TestCase):
    """RB-DEFECT-2026-08-11 regression: the upstream entity classifier
    tagged a Cracker Barrel CEO-departure story to Yum Brands, Bloomin'
    Brands, AND Cracker Barrel -- only the last one is actually mentioned in
    the story. Surfacing "Relevant Activity" broadly (team edition only)
    made this mis-tag visible and misleading."""

    def test_rejects_entity_not_named_in_its_own_evidence(self):
        item = {
            "title": "Yum Brands — Relevant Activity",
            "why_it_matters": "[EXEC DEPARTURE] Cracker Barrel CEO Julie Masino Steps Down",
            "extras": {"entity_name": "Yum Brands"},
        }
        self.assertFalse(rib._watchlist_evidence_names_own_entity(item))

    def test_accepts_entity_named_in_its_own_evidence(self):
        item = {
            "title": "Cracker Barrel — Relevant Activity",
            "why_it_matters": "[EXEC DEPARTURE] Cracker Barrel CEO steps down",
            "extras": {"entity_name": "Cracker Barrel"},
        }
        self.assertTrue(rib._watchlist_evidence_names_own_entity(item))

    def test_title_alone_does_not_satisfy_the_check(self):
        """The title is auto-generated as f'{entity_name} — {status}', so it
        trivially contains the entity name by construction -- only the
        why_it_matters/summary evidence text counts."""
        item = {
            "title": "Yum Brands — Relevant Activity",
            "why_it_matters": "Some unrelated development with no company named.",
            "extras": {"entity_name": "Yum Brands"},
        }
        self.assertFalse(rib._watchlist_evidence_names_own_entity(item))


class TestSponsoredContentExcluded(unittest.TestCase):
    def test_spons_url_marks_item_not_relevant(self):
        item = {
            "title": "Can your tech tell you this? Game-changing ways restaurant operators are using AI",
            "extras": {"source_url": "https://www.restaurantdive.com/spons/some-native-ad/826779/"},
        }
        self.assertTrue(rib._is_sponsored(item))
        self.assertFalse(rib._is_gp_relevant(item, set(), None))

    def test_normal_url_not_flagged_sponsored(self):
        item = {"extras": {"source_url": "https://www.restaurantdive.com/news/toast-ai/827503/"}}
        self.assertFalse(rib._is_sponsored(item))


class TestGarbledSummaryCleaned(unittest.TestCase):
    def test_css_artifact_summary_detected_and_blanked(self):
        garbled = ("]:pointer-events-auto R6Vx5W_threadScrollVars "
                   "scroll-mb-[calc(var(--scroll-root-safe-area-inset-bottom,0px)+var(--x))]")
        self.assertTrue(rib._looks_garbled(garbled))
        items = [{"title": "A real story", "why_it_matters": garbled, "summary": garbled,
                  "extras": {}}]
        cleaned = rib._clean_garbled_summaries(items)
        self.assertEqual(cleaned[0]["why_it_matters"], "")
        self.assertEqual(cleaned[0]["summary"], "")
        self.assertEqual(cleaned[0]["title"], "A real story")

    def test_normal_summary_untouched(self):
        self.assertFalse(rib._looks_garbled("Toast executives say AI adoption is accelerating."))
        items = [{"title": "x", "why_it_matters": "A normal sentence.", "extras": {}}]
        cleaned = rib._clean_garbled_summaries(items)
        self.assertEqual(cleaned[0]["why_it_matters"], "A normal sentence.")


class TestGpEarningsNote(unittest.TestCase):
    """RB-DEFECT-2026-08-11: Todd rejected the templated 'why this matters'
    line as generic filler -- "if all you are going to give me is a generic
    statement then don't bother." The replacement must extract real figures
    from the release text and give a category-specific reason, or add
    nothing at all rather than fall back to boilerplate."""

    def test_extracts_real_metrics_not_generic_text(self):
        text = ("PAR TECHNOLOGY CORPORATION ANNOUNCES SECOND QUARTER 2026 RESULTS "
                 "Quarterly revenues increased 19% year-over-year to $133.4 million "
                 "Annual Recurring Revenue (ARR) increased 17% year-over-year to $338.0 million")
        note = rib._gp_earnings_note(text, "PAR Technology", "restaurant_tech_pos")
        self.assertIn("19%", note)
        self.assertIn("$133.4 million", note)
        self.assertIn("17%", note)
        self.assertNotIn("demand-side read on tech/payments spend appetite", note)  # old boilerplate

    def test_category_drives_a_specific_not_copy_pasted_relevance_reason(self):
        pos_note = rib._gp_earnings_note(
            "Revenue increased 10% year-over-year to $50 million", "Vendor A", "restaurant_tech_pos")
        payments_note = rib._gp_earnings_note(
            "Revenue increased 10% year-over-year to $50 million", "Vendor B", "restaurant_tech_payments")
        self.assertIn("POS competitor", pos_note)
        self.assertIn("payments-processing competitor", payments_note)
        self.assertNotEqual(pos_note.split("Why this matters")[1], payments_note.split("Why this matters")[1])

    def test_no_extractable_metrics_returns_empty_not_generic_filler(self):
        """Olo's actual 8-K text in this real edition was just EDGAR filing
        metadata (Filed/AccNo/Size/Item headers) with no release body -- no
        real figures exist to summarize, so the note must be empty, not a
        fallback generic sentence."""
        text = ("Filed: 2026-08-10 AccNo: 0001213900-26-087318 Size: 420 KB "
                "Item 2.02: Results of Operations and Financial Condition")
        note = rib._gp_earnings_note(text, "Olo", "restaurant_tech_digital")
        self.assertEqual(note, "")

    def test_empty_text_returns_empty(self):
        self.assertEqual(rib._gp_earnings_note("", "Some Company", "restaurant_tech_pos"), "")


class TestTeamSynthesizeBatch(unittest.TestCase):
    """RB-DEFECT-2026-08-21: _team_why_it_matters used to be a pure
    keyword-bucket lookup -- confirmed live, unrelated leadership stories
    (Jack in the Box succession, Pizza Hut CEO exit, Qu CPTO hire, Mike's Red
    Tacos CEO) all rendered the identical generic sentence in one issue.
    _team_synthesize_batch routes stories through the same hardened LLM
    synthesis used for Todd's personal Dot Connections (brief_synthesis.py),
    grounded in each story's own evidence, with the bucket as a fallback
    only."""

    def setUp(self):
        rib._team_synth_why = {}

    def tearDown(self):
        rib._team_synth_why = {}

    def test_synthesized_why_wins_over_bucket_fallback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "team_synth.json"
            with patch.object(rib, "TEAM_SYNTH_CACHE_PATH", cache_path), \
                 patch("brief_synthesis.synthesize_signals", return_value={
                     "jack in the box names president in ceo succession step": {
                         "why": "Taylor Montgomery's promotion signals Jack in the Box is "
                                "grooming an internal successor rather than searching externally.",
                         "action": "No specific action warranted; awareness only.",
                     }
                 }):
                rib._team_synthesize_batch([{
                    "title": "Jack in the Box names president in CEO succession step",
                    "evidence": "Jack in the Box names president in CEO succession step.",
                }])
        why = rib._team_why_it_matters("Jack in the Box names president in CEO succession step")
        self.assertIn("Taylor Montgomery", why)
        self.assertNotIn("Leadership change can reset strategy", why)

    def test_two_different_leadership_stories_get_different_synthesized_text(self):
        """The exact regression: two unrelated leadership stories must not
        collapse to the same sentence once synthesis is wired in."""
        import tempfile

        def _fake_synthesize(items, role_summary):
            return {it["key"]: {"why": f"Specific reason for {it['title']}.", "action": ""}
                    for it in items}

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "team_synth.json"
            with patch.object(rib, "TEAM_SYNTH_CACHE_PATH", cache_path), \
                 patch("brief_synthesis.synthesize_signals", side_effect=_fake_synthesize):
                rib._team_synthesize_batch([
                    {"title": "Jack in the Box names president", "evidence": "x"},
                    {"title": "Mike's Red Tacos Announces Andrew Feghali as CEO", "evidence": "y"},
                ])
        why_a = rib._team_why_it_matters("Jack in the Box names president")
        why_b = rib._team_why_it_matters("Mike's Red Tacos Announces Andrew Feghali as CEO")
        self.assertNotEqual(why_a, why_b)

    def test_synthesis_unavailable_falls_back_to_bucket(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "team_synth.json"
            with patch.object(rib, "TEAM_SYNTH_CACHE_PATH", cache_path), \
                 patch("brief_synthesis.synthesize_signals", return_value=None):
                rib._team_synthesize_batch([{"title": "Some CEO steps down", "evidence": "x"}])
        why = rib._team_why_it_matters("Some CEO steps down")
        self.assertEqual(why, rib._team_why_it_matters_fallback("Some CEO steps down"))

    def test_synthesis_raising_never_breaks_render(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "team_synth.json"
            with patch.object(rib, "TEAM_SYNTH_CACHE_PATH", cache_path), \
                 patch("brief_synthesis.synthesize_signals", side_effect=RuntimeError("boom")):
                rib._team_synthesize_batch([{"title": "A story", "evidence": "x"}])  # must not raise
        why = rib._team_why_it_matters("A story")
        self.assertTrue(why)

    def test_synthesized_result_mentioning_banned_terms_is_dropped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "team_synth.json"
            with patch.object(rib, "TEAM_SYNTH_CACHE_PATH", cache_path), \
                 patch("brief_synthesis.synthesize_signals", return_value={
                     "a story": {"why": "This matters for Global Payments' Genius roadmap.", "action": ""}
                 }):
                rib._team_synthesize_batch([{"title": "A story", "evidence": "x"}])
        why = rib._team_why_it_matters("A story")
        self.assertNotIn("Global Payments", why)
        self.assertNotIn("Genius", why)

    def test_cached_result_persists_across_calls_without_recalling_llm(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "team_synth.json"
            with patch.object(rib, "TEAM_SYNTH_CACHE_PATH", cache_path), \
                 patch("brief_synthesis.synthesize_signals", return_value={
                     "a story": {"why": "Concrete synthesized reason.", "action": ""}
                 }) as mock_synth:
                rib._team_synthesize_batch([{"title": "A story", "evidence": "x"}])
                self.assertEqual(mock_synth.call_count, 1)
                # Second call, same process: already in _team_synth_why, no re-call needed.
                rib._team_synthesize_batch([{"title": "A story", "evidence": "x"}])
                self.assertEqual(mock_synth.call_count, 1)
                # Fresh process (new in-memory dict) but same on-disk cache: still no re-call.
                rib._team_synth_why = {}
                rib._team_synthesize_batch([{"title": "A story", "evidence": "x"}])
                self.assertEqual(mock_synth.call_count, 1)
        why = rib._team_why_it_matters("A story")
        self.assertEqual(why, "Concrete synthesized reason.")


class TestTeamHeadlineSectionDropsNonAnswer(unittest.TestCase):
    """A/B used to ship the "Read this only if..." non-answer unfiltered even
    though D+ already knew to drop it -- confirmed live in the same issue."""

    def test_item_earning_only_the_generic_fallback_is_dropped(self):
        rib._team_synth_why = {}
        items = [{
            "title": "What Influences Diners' Choices in 2026",
            "summary": "A general trend piece with no specific company or theme.",
            "extras": {"source_url": "https://example.com/diners-choices",
                       "source_name": "Modern Restaurant Management", "pub_date": "2026-08-19"},
        }]
        with patch.object(rib, "_team_synthesize_batch", lambda items: None):
            out = rib._render_team_headline_section(items, "A: Restaurant Industry")
        self.assertEqual(out, "")

    def test_item_with_real_relevance_still_renders(self):
        rib._team_synth_why = {}
        items = [{
            "title": "Toast rolls out new loyalty platform for franchise operators",
            "summary": "Detail.",
            "extras": {"source_url": "https://example.com/toast-loyalty",
                       "source_name": "NRN", "pub_date": "2026-08-19"},
        }]
        with patch.object(rib, "_team_synthesize_batch", lambda items: None):
            out = rib._render_team_headline_section(items, "A: Restaurant Industry")
        self.assertIn("Toast rolls out new loyalty platform", out)
        self.assertIn("Why it matters:", out)


class TestTeamWatchlistSynthesizesRealEvidence(unittest.TestCase):
    """F used to render raw upstream why_it_matters/summary text verbatim --
    often just the source press release's own title. It should read like
    every other section: a real relevance sentence, grounded in that text."""

    def test_raw_press_release_title_replaced_with_synthesized_reason(self):
        rib._team_synth_why = {}
        items = [{
            "title": "Einstein Bros. Bagels — New Activity",
            "why_it_matters": "EINSTEIN BROS. BAGELS BRINGS BACK PUMPKIN BAGEL AND PUMPKIN "
                               "SHMEAR; DIGITAL-EXCLUSIVE EARLY ACCESS STARTS AUG. 19 - PR Newswire",
            "extras": {"entity_name": "Einstein Bros. Bagels", "watchlist_status": "New Activity",
                       "source_url": "https://example.com/einstein-pumpkin"},
        }]

        def _fake_batch(candidates):
            for c in candidates:
                rib._team_synth_why[rib._team_synth_key(c["title"])] = (
                    "The digital-exclusive early-access window is a first-party loyalty "
                    "play, not just a seasonal menu refresh."
                )

        with patch.object(rib, "_team_synthesize_batch", _fake_batch):
            out = rib._render_team_watchlist(items, date(2026, 8, 21), {})
        self.assertIn("first-party loyalty", out)
        self.assertNotIn("PR Newswire", out)

    def test_synthesis_unavailable_falls_back_to_truncated_raw_evidence(self):
        rib._team_synth_why = {}
        items = [{
            "title": "Red Lobster — New Activity",
            "why_it_matters": "Some raw press release text about Red Lobster.",
            "extras": {"entity_name": "Red Lobster", "watchlist_status": "New Activity",
                       "source_url": "https://example.com/red-lobster"},
        }]
        with patch.object(rib, "_team_synthesize_batch", lambda items: None):
            out = rib._render_team_watchlist(items, date(2026, 8, 21), {})
        self.assertIn("Some raw press release text about Red Lobster", out)

    def test_rendered_watchlist_url_registered_to_prevent_duplication_in_section_i(self):
        rib._team_synth_why = {}
        prior = rib._rendered_this_run
        rib._rendered_this_run = set()
        try:
            items = [{
                "title": "Domino's — New Activity",
                "why_it_matters": "Domino's press release: Q2 expansion.",
                "extras": {"entity_name": "Domino's", "watchlist_status": "New Activity",
                           "source_url": "https://example.com/dominos-q2"},
            }]
            with patch.object(rib, "_team_synthesize_batch", lambda items: None):
                rib._render_team_watchlist(items, date(2026, 8, 21), {})
            self.assertIn("https://example.com/dominos-q2", rib._rendered_this_run)
        finally:
            rib._rendered_this_run = prior


class TestTeamBusinessSummaryDoesNotRepeatAlreadyShownItems(unittest.TestCase):
    """RB-DEFECT-2026-08-21: Section I used to restate whichever A/B items
    happened to score highest, verbatim -- confirmed live, three bullets in
    one issue were word-for-word duplicates of A/B entries shown just above.
    A synthesis section that repeats what the reader just read isn't
    synthesis."""

    def test_item_already_in_rendered_this_run_is_skipped(self):
        rib._team_synth_why = {}
        prior = rib._rendered_this_run
        rib._rendered_this_run = {"https://example.com/already-shown"}
        try:
            industry_items = [{
                "title": "Potbelly Announces Back-to-School Promotions",
                "summary": "Detail.",
                "extras": {"source_url": "https://example.com/already-shown"},
            }]
            with patch.object(rib, "_team_synthesize_batch", lambda items: None):
                out = rib._render_team_business_summary(industry_items, [], [], "covering Aug 19-21", date(2026, 8, 21), {})
            self.assertNotIn("Potbelly Announces Back-to-School Promotions", out)
            self.assertIn("already covered above", out)
        finally:
            rib._rendered_this_run = prior

    def test_item_ab_dropped_for_non_answer_does_not_leak_into_section_i(self):
        """Live regression caught while verifying this fix: an item A already
        dropped for earning only the "Read this only if..." non-answer (not
        yet in _rendered_this_run, since A never registered its URL) must
        not resurface in I -- I is a second synthesis pass, not a dumping
        ground for what A already rejected as content-free."""
        rib._team_synth_why = {}
        prior = rib._rendered_this_run
        rib._rendered_this_run = set()
        try:
            industry_items = [{
                "title": "What Influences Diners' Choices in 2026",
                "summary": "A general trend piece with no specific company or theme.",
                "extras": {"source_url": "https://example.com/diners-choices"},
            }]
            with patch.object(rib, "_team_synthesize_batch", lambda items: None):
                out = rib._render_team_business_summary(industry_items, [], [], "covering Aug 19-21", date(2026, 8, 21), {})
            self.assertNotIn("Diners' Choices", out)
            self.assertNotIn("Read this only if", out)
        finally:
            rib._rendered_this_run = prior

    def test_item_not_yet_shown_still_renders(self):
        rib._team_synth_why = {}
        prior = rib._rendered_this_run
        rib._rendered_this_run = set()
        try:
            industry_items = [{
                "title": "New POS platform launch nothing else surfaced",
                "summary": "Detail.",
                "extras": {"source_url": "https://example.com/fresh"},
            }]
            with patch.object(rib, "_team_synthesize_batch", lambda items: None):
                out = rib._render_team_business_summary(industry_items, [], [], "covering Aug 19-21", date(2026, 8, 21), {})
            self.assertIn("New POS platform launch nothing else surfaced", out)
        finally:
            rib._rendered_this_run = prior


class TestTeamEditionTopStoryLead(unittest.TestCase):
    """RB-DEFECT-2026-08-21: five roughly-equal-weight sections gave a
    skimming reader no signal about what to read first. A one-line pointer
    to the single highest-scoring story now runs right under the header."""

    def _render(self, tmp_path: Path, sections: dict) -> str:
        cache_path = tmp_path / "daily_brief.json"
        cache_path.write_text(json.dumps(_fake_cache(sections)), encoding="utf-8")
        with patch.object(rib, "DAILY_BRIEF_CACHE", cache_path), \
             patch.object(rib, "BRIEFS_DIR", tmp_path), \
             patch.object(rib, "_team_synthesize_batch", lambda items: None):
            return rib.render_team_edition(date(2026, 8, 21), dry_run=True)

    def test_high_scoring_leadership_story_becomes_the_lead(self):
        import tempfile
        sections = {
            "restaurant_industry_headlines": [
                {"title": "Jack in the Box names president in CEO succession step", "summary": "Detail.",
                 "extras": {"source_url": "https://example.com/jitb", "source_name": "Restaurant Dive",
                            "pub_date": "2026-08-20"}},
            ],
            "watchlist_intelligence": [
                {"title": "Jack in the Box — No Change",
                 "extras": {"entity_name": "Jack in the Box", "watchlist_status": "No Change"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertIn("Today's must-read:", out)
        self.assertIn("Jack in the Box names president", out.split("---")[0] + out.split("---")[1])

    def test_no_scorable_item_omits_the_lead_line(self):
        import tempfile
        sections = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            out = self._render(Path(tmpdir), sections)
        self.assertNotIn("Today's must-read:", out)


if __name__ == "__main__":
    unittest.main()
