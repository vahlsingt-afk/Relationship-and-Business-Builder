#!/usr/bin/env python3
"""
test_value_wedge.py — RB-2026-09-25, rewritten 2026-09-28 for the
three-circle redesign (Company Strengths / Customer Needs / Competitor
Weaknesses). See value_wedge.py's module docstring for what changed and
why: brand_id is now optional (preserves server.py's chat-tool routes and
refresh_persisted_briefs.py's nightly batch job, neither of which has a
brand in the loop); circle 3 now reads vs_genius.genius_advantages
(fixed from the wrong field, vs_genius.competitor_advantages); output is
structured data, not markdown (render_value_wedge_markdown() exists only
for those two legacy callers).

Isolated against a disposable competitor_intelligence_common.ROOT, a
disposable genius_capabilities.GENIUS_CAPABILITIES_PATH, a disposable
ecosystem_intelligence.json path, a disposable brand_profile_common.ROOT,
and disposable artifact_vault_common.VAULT_ROOT / intelligence_index
paths. Never touches real project state. Same isolation pattern as
test_competitive_brief.py.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import value_wedge as vw  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import genius_capabilities as gc  # noqa: E402
import brand_profile_common as bpc  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

TEST_SLUG = "test-fixture-competitor"
TEST_BRAND_ID = "brand-fixture"


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_cic_root = cic.ROOT
        self._orig_gc_path = gc.GENIUS_CAPABILITIES_PATH
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_bpc_root = bpc.ROOT
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH

        cic.ROOT = tmp_root / "competitor_intelligence"
        gc.GENIUS_CAPABILITIES_PATH = tmp_root / "genius_capabilities.json"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = tmp_root / "ecosystem_intelligence.json"
        bpc.ROOT = tmp_root / "brand_profiles"
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
        self.comp = {
            "competitor_id": f"comp-{TEST_SLUG}", "competitor_slug": TEST_SLUG,
            "display_name": "Test Fixture Competitor", "vendor_entity_id": None,
            "competes_on": [],
            "positioning_summary": "", "todds_pov": "",
            "vs_genius": {"genius_advantages": [], "competitor_advantages": []},
            "category_battle_cards": {}, "last_evidence_date": None,
        }
        self._write_comp()

        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps({
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-25",
            "entities": [{"id": TEST_BRAND_ID, "name": "Fixture Brand", "entity_type": "brand"}],
            "relationships": [], "signals": [], "sources": [],
            "assessments": [], "user_relevance": [], "strategic_recommendations": [],
        }), encoding="utf-8")

    def _write_comp(self) -> None:
        (self.comp_dir / "competitor.json").write_text(json.dumps(self.comp), encoding="utf-8")
        if not (self.comp_dir / "evidence.jsonl").exists():
            (self.comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

    def tearDown(self):
        cic.ROOT = self._orig_cic_root
        gc.GENIUS_CAPABILITIES_PATH = self._orig_gc_path
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        bpc.ROOT = self._orig_bpc_root
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestBuildValueWedgeData(_IsolatedFixtureMixin):
    def test_unknown_competitor_raises(self):
        with self.assertRaises(FileNotFoundError):
            vw.build_value_wedge_data("does-not-exist-xyz")

    def test_no_brand_id_leaves_customer_needs_empty(self):
        data = vw.build_value_wedge_data(TEST_SLUG)
        self.assertIsNone(data["brand_id"])
        self.assertIsNone(data["brand_name"])
        self.assertEqual(data["circle2_customer_needs"], [])

    def test_circle3_reads_genius_advantages_not_competitor_advantages(self):
        """The actual bug fix: circle 3 ("competitor weaknesses") must read
        vs_genius.genius_advantages (side="genius" -- Genius wins here,
        meaning the competitor is weak there), never vs_genius.
        competitor_advantages (the competitor's own strengths -- the
        opposite meaning, what the pre-2026-09-28 code read)."""
        self.comp["vs_genius"]["genius_advantages"] = [{"point": "Genius wins on integration depth."}]
        self.comp["vs_genius"]["competitor_advantages"] = [{"point": "They have a cleaner UI."}]
        self._write_comp()
        data = vw.build_value_wedge_data(TEST_SLUG)
        weaknesses = [w["point"] for w in data["circle3_competitor_weaknesses"]]
        self.assertIn("Genius wins on integration depth.", weaknesses)
        self.assertNotIn("They have a cleaner UI.", weaknesses)

    def test_circle3_filters_by_category_keeps_company_wide(self):
        """2026-09-28, caught live: a POS-specific weakness (PAR's RTI/
        Data Central back-office lineage) was showing up on a Loyalty-
        scoped wedge for McDonald's -- plainly off-topic. A point tagged
        with a category only belongs in a wedge scoped to that same
        category; an untagged point is company-wide and always applies."""
        self.comp["competes_on"] = ["loyalty_engagement"]
        self.comp["vs_genius"]["genius_advantages"] = [
            {"point": "POS-specific weakness.", "category": "pos"},
            {"point": "Loyalty-specific weakness.", "category": "loyalty_engagement"},
            {"point": "Company-wide financial weakness."},
        ]
        self._write_comp()
        data = vw.build_value_wedge_data(TEST_SLUG, category="loyalty_engagement")
        weaknesses = [w["point"] for w in data["circle3_competitor_weaknesses"]]
        self.assertIn("Loyalty-specific weakness.", weaknesses)
        self.assertIn("Company-wide financial weakness.", weaknesses)
        self.assertNotIn("POS-specific weakness.", weaknesses)

    def test_circle1_flattens_capabilities_across_competes_on(self):
        self.comp["competes_on"] = ["pos", "payments"]
        self._write_comp()
        gc.add_capability("pos", "Real-time inventory sync", why_it_matters="eliminates manual reconciliation")
        gc.add_capability("payments", "Integrated processing")
        data = vw.build_value_wedge_data(TEST_SLUG)
        points = [c["point"] for c in data["circle1_genius_strengths"]]
        self.assertIn("Real-time inventory sync", points)
        self.assertIn("Integrated processing", points)

    def test_category_filter_scopes_circle1_to_one_product_line(self):
        """2026-09-28, Todd: pick a specific Genius product, not every
        category the vendor happens to compete on."""
        self.comp["competes_on"] = ["pos", "payments"]
        self._write_comp()
        gc.add_capability("pos", "Real-time inventory sync")
        gc.add_capability("payments", "Integrated processing")
        data = vw.build_value_wedge_data(TEST_SLUG, category="pos")
        points = [c["point"] for c in data["circle1_genius_strengths"]]
        self.assertIn("Real-time inventory sync", points)
        self.assertNotIn("Integrated processing", points)
        self.assertEqual(data["category"], "pos")

    def test_category_not_in_competes_on_falls_back_to_all(self):
        self.comp["competes_on"] = ["pos"]
        self._write_comp()
        gc.add_capability("pos", "Real-time inventory sync")
        data = vw.build_value_wedge_data(TEST_SLUG, category="payments")
        points = [c["point"] for c in data["circle1_genius_strengths"]]
        self.assertIn("Real-time inventory sync", points)
        self.assertIsNone(data["category"])

    def test_circle2_includes_known_challenge_signals_alongside_pain_points(self):
        """2026-09-28, Todd: 'populate the customer needs of the brand from
        what we know' -- a brand's already-researched challenge_or_headwind
        signal is a real, sourced difficulty, surfaced without requiring
        anyone to manually re-log it as a pain point."""
        profile = bpc.get_profile(TEST_BRAND_ID, persist=False)
        bpc.add_pain_point(profile, value="Struggling with kiosk uptime.")
        bpc.add_signal(profile, value="Traffic declines cited in Q2 earnings call.",
                        signal_type="challenge_or_headwind")
        bpc.add_signal(profile, value="Unrelated leadership change.", signal_type="leadership_change")
        bpc.save_profile(TEST_BRAND_ID, profile)
        data = vw.build_value_wedge_data(TEST_SLUG, TEST_BRAND_ID)
        values = [n["value"] for n in data["circle2_customer_needs"]]
        self.assertIn("Struggling with kiosk uptime.", values)
        self.assertIn("Traffic declines cited in Q2 earnings call.", values)
        self.assertNotIn("Unrelated leadership change.", values)

    def test_circle2_reads_brand_pain_points_when_brand_id_given(self):
        profile = bpc.get_profile(TEST_BRAND_ID, persist=False)
        bpc.add_pain_point(profile, value="Struggling with kiosk uptime.")
        bpc.save_profile(TEST_BRAND_ID, profile)
        data = vw.build_value_wedge_data(TEST_SLUG, TEST_BRAND_ID)
        self.assertEqual(data["brand_name"], "Fixture Brand")
        values = [n["value"] for n in data["circle2_customer_needs"]]
        self.assertIn("Struggling with kiosk uptime.", values)

    def test_unknown_brand_id_raises(self):
        with self.assertRaises(bpc.NotFoundError):
            vw.build_value_wedge_data(TEST_SLUG, "brand-does-not-exist")


class TestRenderValueWedgeMarkdown(_IsolatedFixtureMixin):
    """Only exercised by server.py's chat-tool routes now (Todd's own
    rbb-chat interface) -- Team Portal renders `data` as a diagram instead."""

    def test_empty_data_renders_all_three_honest_blanks(self):
        data = vw.build_value_wedge_data(TEST_SLUG)
        md = vw.render_value_wedge_markdown(data)
        self.assertIn("No product lines declared", md)
        self.assertIn("account-specific and only populates", md)
        self.assertIn("No competitor-side gap points on file yet", md)

    def test_full_case_renders_all_three_sections(self):
        self.comp["competes_on"] = ["pos"]
        self.comp["vs_genius"]["genius_advantages"] = [{"point": "We win on integration depth."}]
        self._write_comp()
        gc.add_capability("pos", "Real-time inventory sync across 40k locations", why_it_matters="eliminates manual reconciliation")
        data = vw.build_value_wedge_data(TEST_SLUG)
        md = vw.render_value_wedge_markdown(data)
        self.assertIn("Real-time inventory sync across 40k locations", md)
        self.assertIn("eliminates manual reconciliation", md)
        self.assertIn("We win on integration depth.", md)


class TestGenerateValueWedge(_IsolatedFixtureMixin):
    def test_unknown_competitor_auto_creates_a_shell(self):
        result = vw.generate_value_wedge("does-not-exist-xyz")
        self.assertEqual(result["competitor_slug"], "does-not-exist-xyz")
        reg = cic.load_registry()
        slugs = [e["competitor_slug"] for e in reg["registry"]]
        self.assertIn("does-not-exist-xyz", slugs)

    def test_generates_real_content_and_persists_vendor_only(self):
        """No brand_id -- the server.py chat-tool / nightly-refresh shape,
        persistence keyed by plain competitor_slug (unchanged from before
        this redesign)."""
        self.comp["competes_on"] = ["pos"]
        self._write_comp()
        gc.add_capability("pos", "Real-time inventory sync")
        result = vw.generate_value_wedge(TEST_SLUG)
        self.assertEqual(result["version"]["version"], 1)
        self.assertIn("Real-time inventory sync", json.dumps(result["data"]))
        current = vw.get_current_value_wedge(TEST_SLUG, include_content=True)
        self.assertEqual(current["data"]["competitor_slug"], TEST_SLUG)

    def test_generates_brand_scoped_wedge_with_its_own_version_lineage(self):
        """With a brand_id, persistence is keyed by `slug--brand_id` --
        independent from the vendor-only artifact above, so a Team Portal
        account-specific generation never collides with or overwrites the
        chat-tool/nightly-refresh vendor-only one."""
        vw.generate_value_wedge(TEST_SLUG)  # vendor-only, v1
        result = vw.generate_value_wedge(TEST_SLUG, TEST_BRAND_ID)
        self.assertEqual(result["data"]["brand_id"], TEST_BRAND_ID)
        self.assertEqual(result["version"]["version"], 1)  # its own lineage, not v2 of the vendor-only one

        vendor_only = vw.get_current_value_wedge(TEST_SLUG, include_content=True)
        brand_scoped = vw.get_current_value_wedge(TEST_SLUG, TEST_BRAND_ID, include_content=True)
        self.assertIsNone(vendor_only["data"]["brand_id"])
        self.assertEqual(brand_scoped["data"]["brand_id"], TEST_BRAND_ID)

    def test_never_mutates_competitor_json_core_fields(self):
        self.comp["competes_on"] = ["pos"]
        self._write_comp()
        before = json.loads((self.comp_dir / "competitor.json").read_text())
        vw.generate_value_wedge(TEST_SLUG)
        after = json.loads((self.comp_dir / "competitor.json").read_text())
        self.assertEqual(before["competes_on"], after["competes_on"])
        self.assertEqual(before["vs_genius"], after["vs_genius"])

    def test_regenerating_unchanged_content_does_not_create_new_version(self):
        r1 = vw.generate_value_wedge(TEST_SLUG)
        r2 = vw.generate_value_wedge(TEST_SLUG, generated_for="test-run")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 1)

    def test_registers_in_intelligence_index_on_first_version(self):
        vw.generate_value_wedge(TEST_SLUG)
        matches = ix.find("Test Fixture Competitor")
        vw_matches = [m for m in matches if m.get("resource_type") == "value_wedge"]
        self.assertEqual(len(vw_matches), 1)

    def test_does_not_call_sync_from_ecosystem(self):
        """Value Wedge deliberately never syncs evidence -- nothing it
        renders comes from evidence.jsonl. Confirm evidence.jsonl stays
        untouched (empty) across a generate call, unlike competitive_
        brief.py's equivalent test would expect a sync attempt."""
        vw.generate_value_wedge(TEST_SLUG)
        evidence_text = (self.comp_dir / "evidence.jsonl").read_text()
        self.assertEqual(evidence_text, "")


if __name__ == "__main__":
    unittest.main()
