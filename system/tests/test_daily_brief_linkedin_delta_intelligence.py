"""
test_daily_brief_linkedin_delta_intelligence.py — RB 9.20 LinkedIn delta in Daily Brief (T8).

Tests that the Daily Brief CoS layer correctly consumes LinkedIn delta from
the cache and renders it through the CoS lens.

Tests:
  T8k: fresh LinkedIn delta cache is consumed and appears in brief
  T8l: brief includes baseline comparison and delta metrics
  T8m: brief surfaces strategic changes / Who Matters Now
  T8n: brief includes execution option for outreach when promotions exist
  T8o: brief does not say 'file processed successfully' or static import language
  T8p: stale LinkedIn delta is labelled stale and does not drive claims
  T8q: source_unavailable LinkedIn is labelled and brief notes absence
"""
from __future__ import annotations
import sys
import json
import unittest
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cos_judgment as cj
import rb_core as core

TODAY = date(2026, 5, 28)


def _make_fresh_linkedin_cache(tmpdir: Path) -> Path:
    """Write a fresh LinkedIn delta cache with realistic delta content."""
    delta_intelligence = {
        "export_date": TODAY.isoformat(),
        "baseline_comparison": {
            "export_contact_count": 200,
            "baseline_rc_count": 15,
            "contacts_matched_in_baseline": 10,
            "contacts_not_in_baseline": 190,
            "export_date": TODAY.isoformat(),
        },
        "title_changes": [
            {"name": "Alice Smith", "id": "alice-smith", "rc_tier": "inner", "old_title": "Manager", "new_title": "Director", "company": "TechCo", "guardrail": "no_tier_promotion_from_linkedin_edge", "review_required": True},
        ],
        "company_changes": [
            {"name": "Bob Jones", "id": "bob-jones", "rc_tier": "broader", "old_company": "OldCo", "new_company": "NewCo", "guardrail": "no_tier_promotion_from_linkedin_edge", "review_required": True},
        ],
        "promotions": [
            {"name": "Alice Smith", "id": "alice-smith", "rc_tier": "inner", "old_title": "Manager", "new_title": "Director", "company": "TechCo", "suggested_action": "congratulations_outreach", "guardrail": "no_tier_promotion_from_linkedin_edge", "review_required": True},
        ],
        "new_recruiters": [
            {"name": "Carol Recruiter", "company": "StaffingCo", "title": "Senior Recruiter", "connected_on": "2026-05-20", "note": "New LinkedIn recruiter connection"},
        ],
        "outreach_queue": [
            {"name": "Alice Smith", "id": "alice-smith", "reason": "Promotion detected: Manager → Director", "priority": "high", "action_type": "congratulations", "requires_confirmation": True},
        ],
        "monitor_queue": [
            {"name": "Bob Jones", "id": "bob-jones", "reason": "Company change detected: OldCo → NewCo. Verify before updating baseline.", "priority": "medium"},
        ],
        "reactivation_candidates": [],
        "conflicts_held_for_review": [],
        "who_matters_now": [
            {"name": "Alice Smith", "id": "alice-smith", "rc_tier": "inner", "movement": "title_change", "relevance_shift": "possible_new_relevance", "note": "Inner-tier contact promoted."},
        ],
        "graph_mutation": {
            "note": "No automatic mutations were applied. All changes require operator review.",
            "new_connections_observed": 190,
            "title_changes_observed": 1,
            "company_changes_observed": 1,
            "promotions_inferred": 1,
            "conflicts_held_for_review": 0,
            "guardrails_applied": [
                "no_last_touch_mutation_from_linkedin_presence",
                "no_tier_promotion_from_linkedin_edge",
                "operator_conflicts_held_for_review",
            ],
        },
    }
    envelope = {
        "_generated_at": datetime.now().isoformat(timespec="seconds"),
        "_source": "linkedin_ingest.py",
        "_export_file": "/tmp/Connections.csv",
        "delta_intelligence": delta_intelligence,
        "data": delta_intelligence,
    }
    cache_path = tmpdir / "linkedin_ingest_latest.json"
    cache_path.write_text(json.dumps(envelope, indent=2))
    return cache_path


def _make_stale_linkedin_cache(tmpdir: Path) -> Path:
    """Write a stale (10-day-old) LinkedIn delta cache."""
    envelope = {
        "_generated_at": (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds"),
        "_source": "linkedin_ingest.py",
        "delta_intelligence": {},
        "data": {},
    }
    cache_path = tmpdir / "linkedin_ingest_latest.json"
    cache_path.write_text(json.dumps(envelope, indent=2))
    return cache_path


def _base_report() -> dict:
    return {
        "today": TODAY.isoformat(),
        "loops": {"overdue": [], "due_today": [], "this_week": [], "future": [], "closed": []},
        "crossings": [],
        "active_threads": [],
        "drr_top": [],
        "social": {},
        "email": {},
        "calendar": {},
    }


class TestLinkedInDeltaInDailyBrief(unittest.TestCase):

    def test_fresh_delta_is_consumed(self):
        """T8k: fresh LinkedIn delta cache produces available=True in brief."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            _make_fresh_linkedin_cache(Path(tmpdir))
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                li_delta = result["linkedin_relationship_delta"]
                self.assertTrue(li_delta.get("available"), f"Expected fresh LinkedIn delta to be available: {li_delta}")
            finally:
                cj.CACHE_DIR = original

    def test_fresh_delta_includes_baseline_comparison(self):
        """T8l: fresh delta includes baseline_comparison in data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            _make_fresh_linkedin_cache(Path(tmpdir))
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                data = result["linkedin_relationship_delta"].get("data") or {}
                self.assertIn("baseline_comparison", data, "Expected baseline_comparison in delta data")
                bc = data["baseline_comparison"]
                self.assertIn("export_contact_count", bc)
                self.assertIn("contacts_matched_in_baseline", bc)
            finally:
                cj.CACHE_DIR = original

    def test_fresh_delta_includes_who_matters_now(self):
        """T8m: fresh delta surfaces who_matters_now changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            _make_fresh_linkedin_cache(Path(tmpdir))
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                data = result["linkedin_relationship_delta"].get("data") or {}
                wm = data.get("who_matters_now") or []
                self.assertGreater(len(wm), 0, "Expected who_matters_now items in delta")
            finally:
                cj.CACHE_DIR = original

    def test_fresh_delta_produces_outreach_execution_option(self):
        """T8n: promotion in delta produces open_outreach_loop execution option."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            _make_fresh_linkedin_cache(Path(tmpdir))
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                opts = result["execution_options"]
                outreach_opts = [o for o in opts if o["action"] == "open_outreach_loop"]
                self.assertGreater(len(outreach_opts), 0, "Expected open_outreach_loop option from LinkedIn delta")
                for opt in outreach_opts:
                    self.assertTrue(opt["requires_confirmation"], "Outreach options must require confirmation")
            finally:
                cj.CACHE_DIR = original

    def test_stale_delta_labelled_stale(self):
        """T8p: stale LinkedIn delta is labelled stale."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            _make_stale_linkedin_cache(Path(tmpdir))
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                li_delta = result["linkedin_relationship_delta"]
                self.assertFalse(li_delta.get("available"), "Stale delta should not be marked available")
                self.assertEqual(li_delta.get("label"), "stale")
                self.assertTrue(li_delta.get("stale"))
            finally:
                cj.CACHE_DIR = original

    def test_stale_delta_does_not_drive_claims(self):
        """T8p: stale delta produces empty data — cannot drive current-state claims."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            _make_stale_linkedin_cache(Path(tmpdir))
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                li_delta = result["linkedin_relationship_delta"]
                self.assertEqual(li_delta.get("data"), {}, "Stale delta must return empty data")
            finally:
                cj.CACHE_DIR = original

    def test_source_unavailable_labelled(self):
        """T8q: no LinkedIn cache → source_unavailable label."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            # Do NOT create a cache file
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                li_delta = result["linkedin_relationship_delta"]
                self.assertFalse(li_delta.get("available"))
                self.assertEqual(li_delta.get("label"), "source_unavailable")
            finally:
                cj.CACHE_DIR = original

    def test_no_static_import_language_in_delta_output(self):
        """T8o: delta output does not say 'file processed successfully' or 'records parsed'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            _make_fresh_linkedin_cache(Path(tmpdir))
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                import daily_brief as db
                rendered = db._render_linkedin_delta_md({"linkedin_relationship_delta": result["linkedin_relationship_delta"]})
                rendered_text = "\n".join(rendered).lower()
                banned = ["file processed successfully", "records parsed", "contacts imported"]
                for phrase in banned:
                    self.assertNotIn(phrase, rendered_text, f"Banned static import phrase found: '{phrase}'")
            finally:
                cj.CACHE_DIR = original

    def test_delta_guardrail_note_in_output(self):
        """T8g: graph mutation note (no auto-mutations) appears in delta data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            _make_fresh_linkedin_cache(Path(tmpdir))
            try:
                report = _base_report()
                result = cj.build_all(report, TODAY)
                data = result["linkedin_relationship_delta"].get("data") or {}
                gm = data.get("graph_mutation") or {}
                self.assertIn("guardrails_applied", gm, "graph_mutation must include guardrails_applied")
                self.assertIn(
                    "no_last_touch_mutation_from_linkedin_presence",
                    gm["guardrails_applied"],
                )
            finally:
                cj.CACHE_DIR = original


if __name__ == "__main__":
    unittest.main()
