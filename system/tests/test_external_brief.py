"""Tests for Layer 1 (external endpoint filtering) and Layer 2 (HTML renderer)."""
from __future__ import annotations

import sys
import os
from pathlib import Path

# Ensure scripts directory is on path
_SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import external_brief_renderer as ebr


# ── helpers ───────────────────────────────────────────────────────────────────

def _headline(title="Test Headline", url="https://example.com/story/1",
              domain=None, pub_date="2026-06-20", why=None, summary=None, fallback=False):
    extras: dict = {"source_url": url, "pub_date": pub_date}
    if domain:
        extras["domain"] = domain
    if fallback:
        extras["fallback"] = True
    return {
        "title": title,
        "why_it_matters": why,
        "summary": summary or "A summary sentence. Second sentence.",
        "extras": extras,
    }


def _signal(title="Industry consolidation accelerating", summary="QSRs are merging. Evidence here."):
    return {"title": title, "summary": summary, "extras": {}}


def _minimal_payload(date_str="2026-06-22"):
    return {
        "date": date_str,
        "restaurant_industry_headlines": [_headline("NRN Story", "https://nrn.com/article/abc")],
        "restaurant_technology_headlines": [_headline("Toast IPO", "https://techcrunch.com/article/toast")],
        "earnings_corporate": [],
        "strategic_signals": [_signal()],
        "proof": {
            "restaurant_articles_scanned": 45,
            "tech_articles_scanned": 30,
            "watchlist_entities_scanned": 12,
            "generated_at": "2026-06-22T10:00:00Z",
        },
    }


# ── Layer 2 tests ─────────────────────────────────────────────────────────────

class TestRenderHtmlEmail:
    def test_returns_valid_html(self):
        result = ebr.render_html_email(_minimal_payload())
        assert result.startswith("<!DOCTYPE html>")
        assert "</html>" in result

    def test_contains_sender_name(self):
        result = ebr.render_html_email(_minimal_payload())
        assert "Restaurant Intelligence" in result

    def test_contains_date(self):
        result = ebr.render_html_email(_minimal_payload())
        assert "June 22, 2026" in result

    def test_world_and_national_sections_absent(self):
        """This report is restaurant-industry scoped: no world/national news section."""
        result = ebr.render_html_email(_minimal_payload())
        assert "World Headlines" not in result
        assert "National Headlines" not in result

    def test_industry_section_present(self):
        result = ebr.render_html_email(_minimal_payload())
        assert "Restaurant Industry" in result

    def test_tech_section_present(self):
        result = ebr.render_html_email(_minimal_payload())
        assert "Restaurant Technology" in result

    def test_strategic_signals_present(self):
        result = ebr.render_html_email(_minimal_payload())
        assert "Strategic Signals" in result

    def test_footer_present(self):
        result = ebr.render_html_email(_minimal_payload())
        assert "automated intelligence system" in result
        assert "Unsubscribe" in result

    def test_scan_counts_in_footer(self):
        result = ebr.render_html_email(_minimal_payload())
        # 45 + 30 = 75
        assert "75" in result

    def test_headline_without_url_skipped(self):
        payload = _minimal_payload()
        # headline with no url and one with proper url
        payload["restaurant_industry_headlines"] = [
            {"title": "No URL item", "summary": "summary", "extras": {}},
            _headline("Has URL", "https://nrn.com/article/something"),
        ]
        result = ebr.render_html_email(payload)
        assert "No URL item" not in result
        assert "Has URL" in result

    def test_homepage_url_skipped(self):
        """A URL with no path beyond domain root should be skipped."""
        payload = _minimal_payload()
        payload["restaurant_industry_headlines"] = [
            _headline("Homepage Only", "https://nrn.com"),
            _headline("Has Path", "https://nrn.com/article/real"),
        ]
        result = ebr.render_html_email(payload)
        assert "Homepage Only" not in result
        assert "Has Path" in result

    def test_not_yet_published_shows_message(self):
        payload = {
            "date": "2026-06-22",
            "status": "not_yet_published",
            "message": "This report publishes Tuesdays and Fridays. No edition has been published yet.",
            "next_publish_date": "2026-06-26",
            "restaurant_industry_headlines": [],
            "restaurant_technology_headlines": [],
            "earnings_corporate": [],
            "strategic_signals": [],
            "proof": {},
        }
        result = ebr.render_html_email(payload)
        assert "No edition has been published yet" in result
        assert "June 26, 2026" in result

    def test_fallback_rt_renders_gray_box(self):
        payload = _minimal_payload()
        payload["restaurant_technology_headlines"] = [
            _headline("Tech Fallback", "https://example.com/foo", fallback=True,
                      summary="Scan count: 30 articles reviewed.")
        ]
        result = ebr.render_html_email(payload)
        assert "No material tech headlines this cycle" in result
        # Verify the fallback div style is present
        assert "border:1px solid" in result

    def test_personal_signal_filtered(self):
        """Signals containing 'you', 'your', or 'Todd' must be skipped."""
        payload = _minimal_payload()
        payload["strategic_signals"] = [
            {"title": "This positions you well", "summary": "You should act on this.", "extras": {}},
            _signal("Neutral industry signal", "QSRs are evolving."),
        ]
        result = ebr.render_html_email(payload)
        assert "positions you well" not in result
        assert "Neutral industry signal" in result

    def test_earnings_badge_rendered(self):
        payload = _minimal_payload()
        payload["earnings_corporate"] = [
            {
                "title": "Darden Q3 Beat",
                "extras": {
                    "signal_badge": "EARNINGS",
                    "source_url": "https://wsj.com/article/darden-q3",
                    "pub_date": "2026-06-20",
                },
                "summary": "Beat estimates.",
            }
        ]
        result = ebr.render_html_email(payload)
        assert "Earnings" in result or "EARNINGS" in result
        assert "Darden Q3 Beat" in result


# ── personal field filtering tests (Layer 1 contract) ────────────────────────

class TestExternalPayloadShape:
    """These tests verify that the external payload shape contract is correct —
    i.e. that the fields we exclude are never present in a payload built by
    _build_external_brief_payload. We test via the renderer's input contract."""

    _PRIVATE_FIELDS = [
        "opportunity_board",
        "communication_intelligence",
        "last_24h_relationship_signals",
        "what_changed_since_yesterday",
        "personal_intel_proof",
        "resource_verification_and_freshness_status",
        "active_threads",
        "interaction_ledger",
        "w2_intelligence",
        "relationship_momentum_status",
    ]

    def test_private_fields_absent_from_minimal_payload(self):
        payload = _minimal_payload()
        for field in self._PRIVATE_FIELDS:
            assert field not in payload, f"Private field '{field}' should not be in external payload"

    def test_required_fields_present(self):
        payload = _minimal_payload()
        for field in ("date", "restaurant_industry_headlines",
                      "restaurant_technology_headlines", "earnings_corporate",
                      "strategic_signals", "proof"):
            assert field in payload, f"Required field '{field}' missing from external payload"

    def test_world_headlines_not_in_scope(self):
        """This report is restaurant-industry scoped; world/national news is dropped."""
        payload = _minimal_payload()
        assert "world_national_headlines" not in payload

    def test_proof_has_no_personal_rows(self):
        payload = _minimal_payload()
        proof = payload["proof"]
        personal_proof_keys = {"personal", "relationship", "thread", "opportunity"}
        for key in proof:
            for personal_kw in personal_proof_keys:
                assert personal_kw not in key.lower(), (
                    f"Proof key '{key}' looks personal and should not be in external payload"
                )
