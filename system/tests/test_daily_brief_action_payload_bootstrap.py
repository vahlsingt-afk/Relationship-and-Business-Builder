"""
test_daily_brief_action_payload_bootstrap.py — RB 9.41 live action payload guard.

The canonical daily brief already carries active_knowledge_assets and retrieval
protocol specs. This test protects the Custom GPT action wrapper from compacting
those fields away before ChatGPT sees them.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = ROOT / "system" / "api" / "server.py"


def _load_server_module():
    module_name = "rb_api_server_for_daily_brief_payload_tests"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_server = _load_server_module()


def _canonical_fixture() -> dict:
    return {
        "contract": "rb_canonical_daily_brief_v1",
        "section_order": ["active_knowledge_assets", "resource_verification_and_freshness_status"],
        "rendering_rules": [
            "SESSION BOOTSTRAP REQUIRED.",
            "Tier 1 retrieval mandate.",
        ],
        "active_knowledge_assets": [
            {
                "title": "McDonald's US Operations Micro Graph — MOUNTED FOR SESSION",
                "summary": "Activation terms: mcdonalds, mcdonald's, nsn.",
                "recommended_action": "Call getMicroGraphSummary(query=\"McDonald's\").",
                "source_refs": ["graphs/index.json"],
                "extras": {
                    "asset_id": "micro_graph:mcdonalds_us_ops",
                    "asset_type": "micro_graph",
                    "graph_slug": "mcdonalds_us_ops",
                    "activation_terms": ["mcdonalds", "mcdonald's", "nsn"],
                    "retrieval_action": "getMicroGraphSummary",
                    "retrieval_params": {"query": "McDonald's"},
                    "routing_rule": "MOUNTED FOR SESSION",
                    "context_boost_window": "session_scoped",
                    "graceful_failure_mode": "Do not fill from base model.",
                    "status": "active",
                    "node_count": 1347,
                    "edge_count": 14049,
                },
            }
        ],
        "knowledge_retrieval_hierarchy": {
            "contract": "rb_knowledge_retrieval_hierarchy_v1",
        },
        "intelligence_pipeline_spec": {
            "contract": "rb_intelligence_pipeline_v1",
        },
        "session_bootstrap_spec": {
            "contract": "rb_session_bootstrap_v1",
        },
        "sections": {
            "active_knowledge_assets": [],
            "resource_verification_and_freshness_status": [],
        },
    }


class DailyBriefActionPayloadBootstrapTests(unittest.TestCase):
    """Protect getDailyBrief's compact action payload bootstrap fields."""

    def setUp(self):
        self.payload = _server._daily_brief_action_payload(
            _canonical_fixture(),
            date(2026, 5, 31),
            source="test",
        )

    def test_top_level_active_knowledge_assets_survive_compaction(self):
        assets = self.payload.get("active_knowledge_assets") or []
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].get("retrieval_action"), "getMicroGraphSummary")
        self.assertIn("mcdonalds", assets[0].get("activation_terms") or [])

    def test_top_level_session_bootstrap_status_summarizes_mounts(self):
        status = self.payload.get("session_bootstrap_status") or {}
        self.assertEqual(status.get("contract"), "rb_session_bootstrap_status_v1")
        self.assertEqual(status.get("micro_graphs_mounted_count"), 1)
        self.assertFalse(status.get("degraded"))
        mounted = status.get("micro_graphs_mounted") or []
        self.assertEqual(mounted[0].get("status"), "MOUNTED")
        self.assertEqual(mounted[0].get("retrieval_action"), "getMicroGraphSummary")

    def test_canonical_active_knowledge_assets_are_not_duplicated(self):
        canonical = self.payload.get("canonical_brief") or {}
        assets = canonical.get("active_knowledge_assets") or []
        self.assertEqual(assets, [])

    def test_retrieval_specs_absent_from_top_level(self):
        # RB 9.42 / DEFECT-026: the three behavioral spec blobs were removed
        # from the top-level action payload. At ~6KB each they were duplicated
        # inside canonical_brief AND at the top level, pushing the total
        # getDailyBrief response to 83KB. That exhausted the GPT's context
        # budget and prevented follow-on action calls (getMicroGraphSummary).
        # The GPT already has these rules in its system instructions.
        self.assertNotIn("knowledge_retrieval_hierarchy", self.payload)
        self.assertNotIn("intelligence_pipeline_spec", self.payload)
        self.assertNotIn("session_bootstrap_spec", self.payload)

    def test_retrieval_specs_absent_from_compact_canonical_brief(self):
        # Same rationale as test_retrieval_specs_absent_from_top_level.
        # The canonical brief (build_canonical_brief) still contains these
        # specs for MCP/local use — only the compact GPT action payload strips
        # them to stay within context budget.
        canonical = self.payload.get("canonical_brief") or {}
        self.assertNotIn("knowledge_retrieval_hierarchy", canonical)
        self.assertNotIn("intelligence_pipeline_spec", canonical)
        self.assertNotIn("session_bootstrap_spec", canonical)

    def test_live_retrieval_receipt_is_mandatory(self):
        receipt = self.payload.get("retrieval_receipt") or {}
        self.assertTrue(receipt.get("verified"))
        self.assertEqual(receipt.get("brief_date"), "2026-05-31")
        self.assertEqual(receipt.get("source"), "test")
        self.assertTrue(receipt.get("retrieved_at"))

    def test_same_day_execution_report_is_exposed(self):
        original_system_dir = _server.SYSTEM_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                _server.SYSTEM_DIR = Path(temp_dir)
                cache_path = _server.SYSTEM_DIR / ".cache" / "morning_pipeline.json"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps({
                    "execution_report": {
                        "date": "2026-05-31",
                        "refresh_status": "Success",
                        "trust_score": 94,
                        "brief_rebuilt": True,
                        "private_internal_field": "excluded",
                    }
                }), encoding="utf-8")
                payload = _server._daily_brief_action_payload(
                    _canonical_fixture(),
                    date(2026, 5, 31),
                    source="test",
                )
                report = payload.get("execution_report") or {}
                self.assertEqual(report.get("refresh_status"), "Success")
                self.assertEqual(report.get("trust_score"), 94)
                self.assertTrue(report.get("brief_rebuilt"))
                self.assertNotIn("private_internal_field", report)
        finally:
            _server.SYSTEM_DIR = original_system_dir

    def test_action_payload_stays_below_platform_budget(self):
        """A content-heavy brief must remain safely below the 80KB Action cap."""
        canonical = _canonical_fixture()
        sections = {}
        for name in _server._ACTION_BRIEF_SECTIONS:
            sections[name] = [
                {
                    "title": f"{name} item {idx} " + ("T" * 300),
                    "summary": "S" * 900,
                    "why_it_matters": "W" * 900,
                    "recommended_action": "A" * 900,
                    "disposition": "monitor",
                    "grounding": "system_detected",
                    "freshness": "fresh",
                    "confidence": "medium",
                    "source_refs": ["source:" + ("R" * 300)] * 4,
                    "intelligence_lifecycle": {
                        "state": "NEW",
                        "first_seen": "2026-06-10",
                        "brief_report_count": 99,
                        "suppression_reason": "X" * 500,
                    },
                }
                for idx in range(25)
            ]
        canonical["sections"] = sections
        payload = _server._daily_brief_action_payload(
            canonical,
            date(2026, 6, 10),
            source="test",
            execution_report={
                "refresh_status": "Partial",
                "intelligence_health_dashboard": {"sources": [{"detail": "X" * 5000}]},
            },
        )
        payload_bytes = len(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
        self.assertLess(
            payload_bytes,
            80_000,
            f"Action payload is {payload_bytes} bytes; platform budget is 80KB",
        )

    def test_published_path_includes_available_pre_rendered_brief(self):
        original_system_dir = _server.SYSTEM_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                _server.SYSTEM_DIR = Path(temp_dir)
                target = date(2026, 8, 19)
                published = _server.SYSTEM_DIR / "published" / "daily" / target.isoformat()
                published.mkdir(parents=True)
                (published / "brief.json").write_text(json.dumps({
                    "today": target.isoformat(),
                    "generated_at": "2026-08-19T10:00:00Z",
                    "canonical_brief": _canonical_fixture(),
                    "counts": {},
                }), encoding="utf-8")
                briefs = _server.SYSTEM_DIR / "briefs"
                briefs.mkdir()
                markdown = "# Intelligence Brief\n\nVerified pre-rendered content."
                (briefs / "2026-08-19-intelligence-brief.md").write_text(
                    markdown, encoding="utf-8"
                )

                payload = _server._published_daily_brief_payload(target)
                pre_rendered = (payload or {}).get("pre_rendered_brief") or {}
                self.assertTrue(pre_rendered.get("available"))
                self.assertEqual(pre_rendered.get("kind"), "intelligence")
                self.assertEqual(pre_rendered.get("markdown"), markdown)
        finally:
            _server.SYSTEM_DIR = original_system_dir

    def test_intelligence_health_dashboard_proves_linkedin_ingestion(self):
        original_system_dir = _server.SYSTEM_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                _server.SYSTEM_DIR = Path(temp_dir)
                cache_dir = _server.SYSTEM_DIR / ".cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / "source_health.json").write_text(json.dumps({
                    "generated_at": "2026-06-08T11:00:00+00:00",
                    "overall_health": "green",
                    "sources": {
                        "email:personal": {
                            "status": "refreshed",
                            "item_count": 44,
                            "last_refreshed_at": "2026-06-08T10:59:00+00:00",
                            "tier": 1,
                        },
                    },
                }), encoding="utf-8")
                (cache_dir / "linkedin_ingest_latest.json").write_text(json.dumps({
                    "ingest_date": "2026-06-07",
                    "_generated_at": "2026-06-07T12:26:45+00:00",
                    "source_file": "Complete_LinkedInDataExport_06-05-2026.zip.zip",
                    "headline_counts": {
                        "connections_in_export": 2734,
                        "new_connections": 15,
                        "company_changes": 355,
                        "role_changes": 558,
                        "disconnections": 35,
                        "reconnections": 338,
                    },
                    "delta_intelligence": {
                        "trust_statistics": {"confidence_score": 90},
                    },
                }), encoding="utf-8")

                payload = _server._daily_brief_action_payload(
                    _canonical_fixture(),
                    date(2026, 6, 8),
                    source="test",
                    execution_report={"refresh_status": "Partial", "trust_score": 94},
                )
                dashboard = payload.get("intelligence_health_dashboard") or {}
                linkedin = next(
                    row for row in dashboard.get("sources") or []
                    if row.get("source_key") == "linkedin_export"
                )
                self.assertEqual(linkedin.get("status"), "Healthy")
                self.assertEqual(linkedin.get("records_processed"), 2734)
                self.assertEqual(linkedin.get("mutations_detected"), 1286)
                self.assertEqual(linkedin.get("confidence_score"), 90)
                self.assertIsNone(linkedin.get("failure_reason"))
                self.assertIn("Never describe", dashboard.get("proof_rule") or "")
        finally:
            _server.SYSTEM_DIR = original_system_dir


if __name__ == "__main__":
    unittest.main()
