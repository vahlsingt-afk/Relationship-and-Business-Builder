#!/usr/bin/env python3
"""
test_competitive_brief_refresh_queue.py — RB-2026-09-30.

Todd's "Option B": a teammate can request a Competitive Brief refresh
sooner than the weekly Friday EOW pass; the request is only ever
recorded (never generated live), and a separate processing pass
(wired into morning_pipeline.py's scan_steps) regenerates and emails
the result. Isolated against disposable queue/competitor/graph/vault
paths -- never touches real project state.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import competitive_brief_refresh_queue as rq  # noqa: E402
import competitive_brief as cb  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402
import team_portal_email as tpe  # noqa: E402

TEST_SLUG = "test-fixture-competitor"


class TestRefreshQueue(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_queue_path = rq.QUEUE_PATH
        self._orig_cic_root = cic.ROOT
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH

        rq.QUEUE_PATH = tmp_root / "competitive_brief_refresh_requests.jsonl"
        cic.ROOT = tmp_root / "competitor_intelligence"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = tmp_root / "ecosystem_intelligence.json"
        avc.VAULT_ROOT = tmp_root / "artifact_vault"
        ix.INDEX_PATH = tmp_root / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = tmp_root / "intelligence_index_updates.jsonl"

        (cic.ROOT / "_portfolio").mkdir(parents=True)
        (cic.ROOT / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": [{"competitor_slug": TEST_SLUG, "display_name": "Test Fixture Competitor"}]}),
            encoding="utf-8",
        )
        self.comp_dir = cic.ROOT / "competitors" / TEST_SLUG
        self.comp_dir.mkdir(parents=True)
        comp = {
            "competitor_id": f"comp-{TEST_SLUG}", "competitor_slug": TEST_SLUG,
            "display_name": "Test Fixture Competitor", "vendor_entity_id": "vendor-fixture",
            "competes_on": ["pos"],
            "vs_genius": {"genius_advantages": [], "competitor_advantages": []},
            "category_battle_cards": {}, "last_evidence_date": "2026-09-01",
        }
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")
        (self.comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps({
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-30",
            "entities": [], "relationships": [], "signals": [], "sources": [],
            "assessments": [], "user_relevance": [], "strategic_recommendations": [],
        }), encoding="utf-8")

    def tearDown(self):
        rq.QUEUE_PATH = self._orig_queue_path
        cic.ROOT = self._orig_cic_root
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()

    def test_request_refresh_only_appends_never_generates(self):
        with patch.object(cb, "persist_synthesis") as mock_persist:
            rq.request_refresh(competitor_slug=TEST_SLUG, member_id="jsmith", email="jane@example.com")
        mock_persist.assert_not_called()
        self.assertTrue(rq.QUEUE_PATH.exists())
        lines = rq.QUEUE_PATH.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["competitor_slug"], TEST_SLUG)
        self.assertEqual(rec["email"], "jane@example.com")

    def test_process_empty_queue_is_a_noop(self):
        result = rq.process_refresh_queue()
        self.assertEqual(result, {"processed": 0, "emailed": 0})

    def test_process_regenerates_and_emails_requester_then_clears_queue(self):
        rq.request_refresh(competitor_slug=TEST_SLUG, member_id="jsmith", email="jane@example.com")
        with patch.object(cb, "persist_synthesis", return_value={"bottom_line": "x", "themes": ["y"]}):
            with patch.object(tpe, "send_brief", return_value={"sent": True}) as mock_send:
                result = rq.process_refresh_queue()

        self.assertEqual(result, {"processed": 1, "emailed": 1})
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["recipient"], "jane@example.com")
        self.assertIn("Test Fixture Competitor", kwargs["subject"])
        self.assertFalse(rq.QUEUE_PATH.exists())

    def test_process_still_emails_the_brief_when_synthesis_fails(self):
        """Real design decision: an LLM failure shouldn't leave the
        requester with nothing -- the account/evidence sections are
        still genuinely fresh and useful on their own."""
        rq.request_refresh(competitor_slug=TEST_SLUG, member_id="jsmith", email="jane@example.com")
        with patch.object(cb, "persist_synthesis", side_effect=RuntimeError("no API key")):
            with patch.object(tpe, "send_brief", return_value={"sent": True}) as mock_send:
                result = rq.process_refresh_queue()
        self.assertEqual(result["emailed"], 1)
        mock_send.assert_called_once()

    def test_process_dedupes_multiple_requesters_for_the_same_competitor(self):
        """Two teammates requesting the same competitor must trigger
        exactly one regeneration, but both get emailed."""
        rq.request_refresh(competitor_slug=TEST_SLUG, member_id="jsmith", email="jane@example.com")
        rq.request_refresh(competitor_slug=TEST_SLUG, member_id="bwayne", email="bruce@example.com")
        with patch.object(cb, "persist_synthesis", return_value={"bottom_line": "x", "themes": ["y"]}) as mock_persist:
            with patch.object(tpe, "send_brief", return_value={"sent": True}) as mock_send:
                result = rq.process_refresh_queue()
        mock_persist.assert_called_once_with(TEST_SLUG)
        self.assertEqual(mock_send.call_count, 2)
        self.assertEqual(result, {"processed": 1, "emailed": 2})

    def test_requests_with_no_email_are_skipped_not_errored(self):
        rq.request_refresh(competitor_slug=TEST_SLUG, member_id="jsmith", email="")
        with patch.object(cb, "persist_synthesis", return_value={"bottom_line": "x", "themes": ["y"]}):
            with patch.object(tpe, "send_brief") as mock_send:
                result = rq.process_refresh_queue()
        mock_send.assert_not_called()
        self.assertEqual(result["emailed"], 0)


if __name__ == "__main__":
    unittest.main()
