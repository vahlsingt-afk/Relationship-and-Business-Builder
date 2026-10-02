"""
test_technology_lifecycle_api.py — Technology Lifecycle Phase 1 API
(getTechnologyLifecycleProfile/listTechnologyForcingSignals/
createTechnologyForcingSignal).

Calls server.py's route functions directly (bypassing _auth via patch,
isolating technology_lifecycle's store paths to a tmp dir) -- same
pattern as test_competitor_bulk_import.py / test_franchisee_finder_api.py.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import technology_lifecycle as tl  # noqa: E402


class _IsolatedPathsMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig = {
            name: getattr(tl, name) for name in (
                "RELATIONSHIP_EVENTS_PATH", "GOVERNANCE_PATH", "PENETRATION_PATH", "CHANGE_EVENTS_PATH",
                "FORCING_SIGNALS_PATH", "FDD_SOURCES_PATH", "TECHNOLOGY_ECONOMICS_PATH",
                "GOVERNANCE_CHANGE_EVENTS_PATH", "PENETRATION_RECONCILIATION_PATH", "FDD_RESEARCH_GAPS_PATH",
                "ENTITY_RESOLUTION_REVIEW_PATH",
            )
        }
        for name in self._orig:
            setattr(tl, name, tmp / f"{name}.jsonl")

    def tearDown(self):
        for name, path in self._orig.items():
            setattr(tl, name, path)
        self._tmpdir.cleanup()


class TestGetTechnologyLifecycleProfile(_IsolatedPathsMixin, unittest.TestCase):
    def test_empty_profile_for_untracked_but_resolvable_brand(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_technology_lifecycle_profile("Burger King", x_api_key=None)
        self.assertEqual(result["relationships"], [])
        self.assertEqual(result["forcing_signals"], [])

    def test_unresolvable_brand_raises_404(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.get_technology_lifecycle_profile("Totally Not A Real Brand XYZ123", x_api_key=None)
        self.assertEqual(cm.exception.status_code, 404)

    def test_populated_profile_reflects_real_write(self):
        import server
        tl.record_relationship_event(
            event_id="e1",
            relationship_key={
                "parent_entity_id": None, "brand_entity_id": "brand-burger-king", "operator_entity_id": None,
                "technology_category": "pos", "vendor_entity_id": "vendor-par-technology", "product": "PAR Brink POS",
            },
            entity_level="brand", lifecycle_state="deployed", state_date_or_range="2024",
            evidence="e", source_url=None, source_type="x", confidence="high", evidence_type="independent_evidence",
        )
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_technology_lifecycle_profile("Burger King", x_api_key=None)
        self.assertEqual(len(result["relationships"]), 1)
        self.assertEqual(result["relationships"][0]["current_state"]["lifecycle_state"], "deployed")


class TestListTechnologyForcingSignals(_IsolatedPathsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        tl.record_forcing_signal(
            signal_id="s1", brand_entity_id="brand-burger-king", entity_level="brand",
            technology_category="pos_hardware", forcing_event_type="os_eol", detail="d",
            evidence="e", source_url=None, confidence="medium", evidence_type="rbb_inference",
        )

    def test_filters_by_brand_name(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_technology_forcing_signals(brand_name="Burger King", technology_category=None, x_api_key=None)
        self.assertEqual(result["signal_count"], 1)

    def test_no_match_for_unrelated_brand(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_technology_forcing_signals(brand_name="McDonald's", technology_category=None, x_api_key=None)
        self.assertEqual(result["signal_count"], 0)

    def test_invalid_category_rejected(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.get_technology_forcing_signals(brand_name=None, technology_category="not_a_real_category", x_api_key=None)
        self.assertEqual(cm.exception.status_code, 422)


class TestCreateTechnologyForcingSignal(_IsolatedPathsMixin, unittest.TestCase):
    def test_creates_real_signal(self):
        import server
        body = server.CreateTechnologyForcingSignalBody(
            brand_name="Burger King", technology_category="pos_hardware", forcing_event_type="os_eol",
            detail="test", evidence="test evidence", confidence="medium", evidence_type="rbb_inference",
        )
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.post_create_technology_forcing_signal(body, x_api_key=None)
        self.assertTrue(result["ok"])
        self.assertEqual(len(tl.load_records(tl.FORCING_SIGNALS_PATH)), 1)

    def test_unresolvable_brand_raises_404(self):
        import server
        from fastapi import HTTPException
        body = server.CreateTechnologyForcingSignalBody(
            brand_name="Totally Not A Real Brand XYZ123", technology_category="pos_hardware",
            forcing_event_type="os_eol", detail="test", evidence="test evidence",
            confidence="medium", evidence_type="rbb_inference",
        )
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.post_create_technology_forcing_signal(body, x_api_key=None)
        self.assertEqual(cm.exception.status_code, 404)

    def test_invalid_evidence_type_rejected_with_422(self):
        import server
        from fastapi import HTTPException
        body = server.CreateTechnologyForcingSignalBody(
            brand_name="Burger King", technology_category="pos_hardware", forcing_event_type="os_eol",
            detail="test", evidence="test evidence", confidence="medium", evidence_type="made_up_type",
        )
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.post_create_technology_forcing_signal(body, x_api_key=None)
        self.assertEqual(cm.exception.status_code, 422)


class TestListFddResearchGaps(_IsolatedPathsMixin, unittest.TestCase):
    def test_empty_when_nothing_on_file(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_fdd_research_gaps(brand_name=None, x_api_key=None)
        self.assertEqual(result["gap_count"], 0)

    def test_filters_by_brand_name(self):
        import server
        tl.record_fdd_research_gap(
            fdd_gap_id="gap-1", brand_id="brand-burger-king", gap_type="governance_unknown", detail="d",
        )
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_fdd_research_gaps(brand_name="Burger King", x_api_key=None)
            result_other = server.get_fdd_research_gaps(brand_name="McDonald's", x_api_key=None)
        self.assertEqual(result["gap_count"], 1)
        self.assertEqual(result_other["gap_count"], 0)

    def test_unresolvable_brand_raises_404(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.get_fdd_research_gaps(brand_name="Totally Not A Real Brand XYZ123", x_api_key=None)
        self.assertEqual(cm.exception.status_code, 404)


class TestEntityResolutionReviewQueue(_IsolatedPathsMixin, unittest.TestCase):
    def test_defaults_to_pending_status(self):
        import server
        tl.queue_entity_resolution_review(review_id="r1", raw_name_or_identifier="X", context="c")
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_entity_resolution_review_queue(status="pending", x_api_key=None)
        self.assertEqual(result["item_count"], 1)

    def test_invalid_status_rejected_with_422(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.get_entity_resolution_review_queue(status="not_a_real_status", x_api_key=None)
        self.assertEqual(cm.exception.status_code, 422)

    def test_resolve_writes_real_entity_id(self):
        import server
        tl.queue_entity_resolution_review(review_id="r1", raw_name_or_identifier="PAR", context="c")
        body = server.ResolveEntityResolutionReviewBody(resolved_entity_name="PAR Technology")
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.post_resolve_entity_resolution_review("r1", body, x_api_key=None)
        self.assertEqual(result["item"]["status"], "resolved")
        self.assertEqual(result["item"]["resolved_entity_id"], "vendor-par-technology")
        self.assertEqual(tl.list_entity_resolution_review("pending"), [])

    def test_resolve_unknown_review_id_404(self):
        import server
        from fastapi import HTTPException
        body = server.ResolveEntityResolutionReviewBody(resolved_entity_name="PAR Technology")
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.post_resolve_entity_resolution_review("not-a-real-review-id", body, x_api_key=None)
        self.assertEqual(cm.exception.status_code, 404)

    def test_resolve_unresolvable_entity_name_404(self):
        import server
        from fastapi import HTTPException
        tl.queue_entity_resolution_review(review_id="r1", raw_name_or_identifier="X", context="c")
        body = server.ResolveEntityResolutionReviewBody(resolved_entity_name="Totally Not A Real Entity XYZ123")
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.post_resolve_entity_resolution_review("r1", body, x_api_key=None)
        self.assertEqual(cm.exception.status_code, 404)

    def test_reject_marks_rejected_without_resolving(self):
        import server
        tl.queue_entity_resolution_review(review_id="r1", raw_name_or_identifier="X", context="c")
        body = server.RejectEntityResolutionReviewBody(reason="duplicate mention")
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.post_reject_entity_resolution_review("r1", body, x_api_key=None)
        self.assertEqual(result["item"]["status"], "rejected")
        self.assertIsNone(result["item"]["resolved_entity_id"])
        self.assertEqual(tl.list_entity_resolution_review("pending"), [])


if __name__ == "__main__":
    unittest.main()
