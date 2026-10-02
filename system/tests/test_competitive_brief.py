#!/usr/bin/env python3
"""
test_competitive_brief.py — RB-2026-09-07.

Isolated against a disposable competitor_intelligence_common.ROOT, a
disposable ecosystem_intelligence.json path (sync_from_ecosystem() reads
the graph too), and disposable artifact_vault_common.VAULT_ROOT /
intelligence_index paths. Never touches real project state.
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

import competitive_brief as cb  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

TEST_SLUG = "test-fixture-competitor"


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_cic_root = cic.ROOT
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH

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
            "display_name": "Test Fixture Competitor", "vendor_entity_id": None,
            "competes_on": ["pos"],
            "positioning_summary": "Real positioning for the fixture competitor.",
            "todds_pov": "Real Todd's POV for the fixture competitor.",
            "vs_genius": {"genius_advantages": [], "competitor_advantages": []},
            "category_battle_cards": {}, "last_evidence_date": "2026-09-01",
        }
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")
        (self.comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps({
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-07",
            "entities": [], "relationships": [], "signals": [], "sources": [],
            "assessments": [], "user_relevance": [], "strategic_recommendations": [],
        }), encoding="utf-8")

    def tearDown(self):
        cic.ROOT = self._orig_cic_root
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestGenerateCompetitiveBrief(_IsolatedFixtureMixin):
    def test_unknown_competitor_auto_creates_a_shell(self):
        """RB-2026-09-25, Todd's explicit direction: reverses the original
        strict-on-purpose design (see test_typo_creates_a_new_shell_now
        below) -- an unrecognized slug now auto-creates a mostly-empty
        shell rather than raising."""
        result = cb.generate_competitive_brief("does-not-exist-xyz")
        self.assertEqual(result["competitor_slug"], "does-not-exist-xyz")
        reg = cic.load_registry()
        slugs = [e["competitor_slug"] for e in reg["registry"]]
        self.assertIn("does-not-exist-xyz", slugs)

    def test_generates_real_content_and_persists(self):
        result = cb.generate_competitive_brief(TEST_SLUG)
        self.assertEqual(result["version"]["version"], 1)
        self.assertIn("Test Fixture Competitor — Competitive Brief", result["markdown"])
        # RB-2026-09-28: Competitive Brief is now a situational document,
        # not a re-render of Competitor Profile -- it must never surface
        # Todd's POV or positioning_summary (that's Profile's job).
        self.assertNotIn("Real positioning for the fixture competitor.", result["markdown"])
        self.assertNotIn("Real Todd's POV for the fixture competitor.", result["markdown"])
        current = cb.get_current_competitive_brief(TEST_SLUG, include_content=True)
        self.assertIn("Test Fixture Competitor", current["content"])

    def test_situational_content_live_accounts_and_recent_evidence(self):
        """The actual differentiator from Competitor Profile: which live
        accounts currently have this vendor in place, and what changed
        recently."""
        comp = json.loads((self.comp_dir / "competitor.json").read_text())
        comp["vendor_entity_id"] = "vendor-fixture"
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")

        import datetime
        recent_date = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
        stale_date = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
        (self.comp_dir / "evidence.jsonl").write_text(
            json.dumps({"evidence_id": "e1", "category": "other", "event_at": recent_date,
                        "summary": "A recent, real development.", "source": "trade press"}) + "\n" +
            json.dumps({"evidence_id": "e2", "category": "other", "event_at": stale_date,
                        "summary": "An old development outside the window.", "source": "trade press"}) + "\n" +
            json.dumps({"evidence_id": "ref-0001", "category": "other", "event_at": recent_date,
                        "summary": "Auto-linked excerpt that should never render here.",
                        "source": "some.docx", "_source_title": "some.docx"}) + "\n",
            encoding="utf-8",
        )

        graph = json.loads(ei.core.ECOSYSTEM_INTELLIGENCE_PATH.read_text())
        graph["entities"] = [{"id": "brand-fixture", "name": "Fixture Brand", "entity_type": "brand"}]
        graph["relationships"] = [{
            "id": "rel-fixture", "from_entity_id": "brand-fixture", "to_entity_id": "vendor-fixture",
            "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
        }]
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps(graph), encoding="utf-8")

        result = cb.generate_competitive_brief(TEST_SLUG)
        md = result["markdown"]
        self.assertIn("Fixture Brand", md)
        self.assertIn("A recent, real development.", md)
        self.assertNotIn("An old development outside the window.", md)
        self.assertNotIn("Auto-linked excerpt that should never render here.", md)

    def test_duplicate_relationships_for_same_brand_category_shown_once(self):
        """Real 2026-09-30 finding (Todd, live): PAR Technology's brief
        listed "Mr. Pickle's Sandwich Shop (POS)" twice. Root cause:
        two separate active uses_vendor_for_category relationship
        records for the exact same (brand, vendor, category) -- 42 such
        duplicate pairs exist graph-wide, most from a gap-fill ingestion
        that duplicated an already-existing relationship rather than
        recognizing it. The account must appear only once regardless of
        how many redundant relationship records point at it."""
        comp = json.loads((self.comp_dir / "competitor.json").read_text())
        comp["vendor_entity_id"] = "vendor-fixture"
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")

        graph = json.loads(ei.core.ECOSYSTEM_INTELLIGENCE_PATH.read_text())
        graph["entities"] = [{"id": "brand-fixture", "name": "Fixture Brand", "entity_type": "brand"}]
        graph["relationships"] = [
            {"id": "rel-fixture-a", "from_entity_id": "brand-fixture", "to_entity_id": "vendor-fixture",
             "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active"},
            {"id": "rel-gapfill-dup", "from_entity_id": "brand-fixture", "to_entity_id": "vendor-fixture",
             "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active"},
        ]
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps(graph), encoding="utf-8")

        result = cb.generate_competitive_brief(TEST_SLUG)
        md = result["markdown"]
        self.assertEqual(md.count("Fixture Brand"), 1)

    def test_duplicate_picks_the_stronger_record_not_an_arbitrary_one(self):
        """Real 2026-09-30 finding: Mr. Pickle's Sandwich Shop had two
        relationship records -- one system_of_record_pos/
        partially_substantiated, one role:unknown/provisional. The brief
        must reflect the better-evidenced record's caveat (or lack of
        one), not whichever happened to be listed first."""
        comp = json.loads((self.comp_dir / "competitor.json").read_text())
        comp["vendor_entity_id"] = "vendor-fixture"
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")

        graph = json.loads(ei.core.ECOSYSTEM_INTELLIGENCE_PATH.read_text())
        graph["entities"] = [{"id": "brand-fixture", "name": "Fixture Brand", "entity_type": "brand"}]
        graph["relationships"] = [
            {"id": "rel-weak", "from_entity_id": "brand-fixture", "to_entity_id": "vendor-fixture",
             "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
             "vendor_role": "unknown", "evidence_posture": "provisional"},
            {"id": "rel-strong", "from_entity_id": "brand-fixture", "to_entity_id": "vendor-fixture",
             "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
             "vendor_role": "system_of_record_pos", "evidence_posture": "substantiated"},
        ]
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps(graph), encoding="utf-8")

        md = cb.generate_competitive_brief(TEST_SLUG)["markdown"]
        self.assertIn("**Confirmed, broad-scope:**\n- Fixture Brand", md)
        self.assertNotIn("Fixture Brand (POS) --", md)

    def test_caveats_flag_narrow_or_unconfirmed_relationships(self):
        """Real 2026-09-30 finding (Todd, live): "I also don't think the
        where we're competing with them right now section is likely
        accurate." Confirmed for PAR Technology -- an approved-hardware-
        only relationship (McDonald's) and an explicitly non-brand-
        standard franchisee deployment (Taco Bell) were both rendered as
        flat, unqualified "(POS)"/"(Labor Workforce)" bullets identical
        in presentation to genuine brand-wide wins."""
        comp = json.loads((self.comp_dir / "competitor.json").read_text())
        comp["vendor_entity_id"] = "vendor-fixture"
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")

        graph = json.loads(ei.core.ECOSYSTEM_INTELLIGENCE_PATH.read_text())
        graph["entities"] = [
            {"id": "brand-confirmed", "name": "Confirmed Brand", "entity_type": "brand"},
            {"id": "brand-hardware-only", "name": "Hardware Only Brand", "entity_type": "brand"},
            {"id": "brand-one-franchisee", "name": "One Franchisee Brand", "entity_type": "brand"},
        ]
        graph["relationships"] = [
            {"id": "rel-confirmed", "from_entity_id": "brand-confirmed", "to_entity_id": "vendor-fixture",
             "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
             "vendor_role": "system_of_record_pos", "deployment_status": "brand_wide_deployment"},
            {"id": "rel-hardware", "from_entity_id": "brand-hardware-only", "to_entity_id": "vendor-fixture",
             "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
             "vendor_role": "approved_hardware_vendor"},
            {"id": "rel-franchisee", "from_entity_id": "brand-one-franchisee", "to_entity_id": "vendor-fixture",
             "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
             "vendor_role": "franchisee_deployment", "deployment_status": "franchisee_deployment_not_brand_standard"},
        ]
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps(graph), encoding="utf-8")

        md = cb.generate_competitive_brief(TEST_SLUG)["markdown"]
        self.assertIn("**Confirmed, broad-scope:**\n- Confirmed Brand", md)
        self.assertIn("Hardware Only Brand (POS) -- approved hardware vendor, not system of record", md)
        self.assertIn("One Franchisee Brand (POS) -- franchisee-level relationship, not confirmed brand-wide", md)
        # No double-statement of the same franchisee-only fact.
        self.assertNotIn("not brand standard", md)

    def test_typo_creates_a_new_shell_now(self):
        """RB-2026-09-25: a typo'd slug ("competitro" for "competitor")
        no longer raises -- it creates a genuinely new, separate,
        mostly-empty shell alongside the real fixture competitor, rather
        than being matched to it. Accepted tradeoff, Todd's explicit call
        (see generate_competitive_brief()'s docstring)."""
        result = cb.generate_competitive_brief("test-fixture-competitro")  # typo
        self.assertEqual(result["competitor_slug"], "test-fixture-competitro")
        reg = cic.load_registry()
        slugs = [e["competitor_slug"] for e in reg["registry"]]
        self.assertIn(TEST_SLUG, slugs)
        self.assertIn("test-fixture-competitro", slugs)
        self.assertEqual(len(reg["registry"]), 2)

    def test_never_mutates_competitor_json_core_fields(self):
        before = json.loads((self.comp_dir / "competitor.json").read_text())
        cb.generate_competitive_brief(TEST_SLUG)
        after = json.loads((self.comp_dir / "competitor.json").read_text())
        self.assertEqual(before["positioning_summary"], after["positioning_summary"])
        self.assertEqual(before["todds_pov"], after["todds_pov"])

    def test_regenerating_unchanged_content_does_not_create_new_version(self):
        """artifact_vault_common.register_version()'s no-op guard
        (2026-09-25) -- unchanged content stays version 1 rather than
        bumping every time the daily refresh re-renders it. A real content
        change still versions correctly; see
        test_artifact_vault_common.py for that coverage directly."""
        r1 = cb.generate_competitive_brief(TEST_SLUG)
        r2 = cb.generate_competitive_brief(TEST_SLUG, generated_for="test-run")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 1)

    def test_registers_in_intelligence_index_on_first_version(self):
        cb.generate_competitive_brief(TEST_SLUG)
        matches = ix.find("Test Fixture Competitor")
        cb_matches = [m for m in matches if m.get("resource_type") == "competitive_brief"]
        self.assertEqual(len(cb_matches), 1)


def _mock_openai_response(payload: dict):
    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = json.dumps(payload)
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


class TestSynthesis(_IsolatedFixtureMixin):
    """Real 2026-09-30 direction (Todd): "Anything that is in the team
    portal that requires CoS commentary should be generated on a weekly
    basis as a part of our week end of week schedule... There are no on
    demand request or real-time reports in the team portal option."
    generate_synthesis()/persist_synthesis() are the weekly-scheduled
    path; render_competitive_brief() only ever reads what they wrote,
    never calls an LLM itself."""

    def test_no_api_key_returns_none_without_calling_openai(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("openai.OpenAI") as mock_cls:
                self.assertIsNone(cb.generate_synthesis(TEST_SLUG))
            mock_cls.assert_not_called()

    def test_persist_writes_onto_competitor_json_and_render_reads_it(self):
        comp = json.loads((self.comp_dir / "competitor.json").read_text())
        comp["vendor_entity_id"] = "vendor-fixture"
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")

        mock_client = _mock_openai_response({
            "bottom_line": "This competitor is a moderate threat with one confirmed broad-scope account.",
            "themes": ["Real theme one.", "Real theme two."],
        })
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = cb.persist_synthesis(TEST_SLUG)

        self.assertIsNotNone(result)
        self.assertEqual(result["bottom_line"], "This competitor is a moderate threat with one confirmed broad-scope account.")
        self.assertIn("generated_at", result)

        saved = json.loads((self.comp_dir / "competitor.json").read_text())
        self.assertEqual(saved["synthesis"]["bottom_line"], result["bottom_line"])

        md = cb.render_competitive_brief(TEST_SLUG)
        self.assertIn("## Bottom line", md)
        self.assertIn("This competitor is a moderate threat", md)
        self.assertIn("Real theme one.", md)
        self.assertIn("AI-synthesized from the evidence below", md)

    def test_render_shows_not_yet_generated_placeholder_when_absent(self):
        md = cb.render_competitive_brief(TEST_SLUG)
        self.assertIn("## Bottom line", md)
        self.assertIn("Not yet generated -- synthesis runs weekly", md)

    def test_failed_llm_call_never_overwrites_a_prior_good_synthesis(self):
        comp = json.loads((self.comp_dir / "competitor.json").read_text())
        comp["vendor_entity_id"] = "vendor-fixture"
        comp["synthesis"] = {"bottom_line": "Prior good synthesis.", "themes": ["Prior theme."], "generated_at": "2026-09-23T00:00:00Z"}
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = cb.persist_synthesis(TEST_SLUG)

        self.assertIsNone(result)
        saved = json.loads((self.comp_dir / "competitor.json").read_text())
        self.assertEqual(saved["synthesis"]["bottom_line"], "Prior good synthesis.")

    def test_synthesize_weekly_briefs_skips_competitors_with_no_vendor_entity_id(self):
        """A mostly-empty auto-created shell (no vendor_entity_id) has
        nothing real to synthesize from -- must be skipped, not attempted."""
        cb.generate_competitive_brief("no-vendor-shell")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=_mock_openai_response(
                {"bottom_line": "x", "themes": ["y"]}
            )):
                results = cb.synthesize_weekly_briefs()
        self.assertNotIn("no-vendor-shell", results)

    def test_synthesize_weekly_briefs_only_slug_filters_to_one(self):
        comp = json.loads((self.comp_dir / "competitor.json").read_text())
        comp["vendor_entity_id"] = "vendor-fixture"
        (self.comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")
        cb.generate_competitive_brief("no-vendor-shell")

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=_mock_openai_response(
                {"bottom_line": "x", "themes": ["y"]}
            )):
                results = cb.synthesize_weekly_briefs(only_slug=TEST_SLUG)
        self.assertEqual(set(results.keys()), {TEST_SLUG})


if __name__ == "__main__":
    unittest.main()
