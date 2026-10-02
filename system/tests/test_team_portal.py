"""
test_team_portal.py

Regression coverage for the Team Portal (system/api/team_portal_api.py +
system/scripts/team_tech_stack.py + system/scripts/team_portal_admin.py) —
the first multi-user-facing surface in RBB. Three things this must
guarantee, all explicitly decided by Todd on 2026-08-29:

1. Team-facing tech-stack reads never leak Todd's editorial layer (notes,
   assessments, risk, strategic_note) — a positive whitelist test, not
   just "the intended fields are present."
2. Writes only ever touch EXISTING brand/vendor entities (404, no
   creation, on an unknown id) and always go through ecosystem_
   intelligence.py's governed write path (_write_graph — never a raw
   json.dump), tagging the record with who made the edit.
3. Per-teammate auth actually gates every /api/* route, with no shared-
   secret fallback, and revocation takes effect immediately.

4. The team-facing Background Brief is an ALLOWLIST of four structural
   sections (Brand Profile, Technology Environment, Leadership, Related
   Artifacts), not a redacted version of the full brief. Confirmed live
   2026-08-29: an earlier version tried redacting just the one section
   that verbatim-quotes Todd's account_intelligence/*.md notes, and real
   testing against a real McDonald's brief showed his sales strategy,
   named colleagues, and competitive tactics still leaking through other
   sections (Bottom Line, Recommended Approach, dynamic free-text fields)
   the blocklist never anticipated. The Competitor Profile keeps a
   blocklist (only "Todd's POV" is stripped) — confirmed lower-stakes and
   explicitly approved as-is.
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import team_tech_stack as tts  # noqa: E402
import account_background_brief as abb  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402

try:
    from fastapi.testclient import TestClient
    import team_portal_api
    import team_portal_email as tpe  # noqa: E402
    _FASTAPI_OK = True
except ImportError:  # pragma: no cover
    _FASTAPI_OK = False


def _graph_fixture() -> dict:
    return {
        "version": 1,
        "contract": "rb_ecosystem_intelligence_v1",
        "last_updated": "2026-08-29",
        "domain_packs": ["restaurants"],
        "entities": [
            {
                "id": "brand-acme-burgers",
                "name": "Acme Burgers",
                "entity_type": "brand",
                "subtype": "restaurant_brand",
                "status": "active",
                "domains": ["restaurants"],
                "attributes": {"rank": 42, "segment": "LSR", "unit_count": 300},
                "sources": ["src-technomic"],
                "confidence": {"level": "medium"},
                # The exact editorial-layer content a team-facing read must
                # never surface — non-empty and distinctive on purpose so a
                # leak is unmistakable, not a coincidence of empty strings.
                "notes": "Todd's private read: this account is a soft target, don't tip our hand.",
            },
            {
                "id": "vendor-zeta-pos",
                "name": "Zeta POS",
                "entity_type": "vendor",
                "subtype": "restaurant_technology_vendor",
                "status": "active",
                "domains": ["restaurants"],
                "attributes": {"primary_category": "pos"},
                "sources": ["src-technomic"],
                "confidence": {"level": "medium"},
            },
        ],
        "relationships": [
            {
                "id": "rel-brand-acme-burgers-pos-system-of-record-pos-vendor-zeta-pos",
                "relationship_type": "uses_vendor_for_category",
                "from_entity_id": "brand-acme-burgers",
                "to_entity_id": "vendor-zeta-pos",
                "category": "pos",
                "vendor_role": "system_of_record_pos",
                "product": "Zeta Core",
                "status": "active",
                "deployment": {"stage": "full_deployment", "penetration_pct": 95},
                "evidence_posture": "substantiated",
                "risk": "red",
                "strategic_note": "Todd's private read: Zeta's renewal is at risk, opening for us.",
                "sources": ["src-technomic"],
                "confidence": {"level": "high"},
            }
        ],
        "assessments": [],
        "signals": [],
        "sources": [{"id": "src-technomic", "source_type": "technomic", "title": "Technomic Top 1500", "captured_at": "2026-08-01"}],
        "user_relevance": [],
        "strategic_recommendations": [],
    }


class TestTechStackWhitelist(unittest.TestCase):
    """No FastAPI needed — exercises team_tech_stack.py directly against a
    monkeypatched graph path, following this codebase's established
    convention (see test_api_ecosystem_graph.py / test_relationship_health_
    aggregate.py) of patching core.ECOSYSTEM_INTELLIGENCE_PATH /
    core.SNAPSHOTS_DIR rather than touching the real 11MB production file."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.graph_path = tmp / "ecosystem_intelligence.json"
        self.graph_path.write_text(json.dumps(_graph_fixture()), encoding="utf-8")
        self.snapshots_dir = tmp / "_snapshots"
        self._path_patch = patch.object(tts.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path)
        self._snap_patch = patch.object(tts.ei.core, "SNAPSHOTS_DIR", self.snapshots_dir)
        self._path_patch.start()
        self._snap_patch.start()

    def tearDown(self):
        self._path_patch.stop()
        self._snap_patch.stop()
        self.tmpdir.cleanup()

    def test_search_brands_finds_fixture(self):
        results = tts.search_brands("acme")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "brand-acme-burgers")

    def test_search_vendors_finds_fixture(self):
        results = tts.search_vendors("zeta")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "vendor-zeta-pos")

    def test_search_vendors_competes_on_category_filters_to_declared_competitors(self):
        """2026-09-28, Todd: 'we should always go 1-1 product... a drop
        down list of POS competitors' once a Genius product is picked --
        never every tracked vendor."""
        import tempfile as _tempfile
        cic_tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(cic_tmp.cleanup)
        cic_root = Path(cic_tmp.name)
        with patch.object(tts.cic, "ROOT", cic_root):
            (cic_root / "_portfolio").mkdir(parents=True)
            (cic_root / "_portfolio" / "competitor_registry.json").write_text(json.dumps({
                "registry": [{"competitor_slug": "zeta-pos", "display_name": "Zeta POS"}],
            }), encoding="utf-8")
            comp_dir = cic_root / "competitors" / "zeta-pos"
            comp_dir.mkdir(parents=True)
            (comp_dir / "competitor.json").write_text(json.dumps({
                "competitor_slug": "zeta-pos", "display_name": "Zeta POS",
                "vendor_entity_id": "vendor-zeta-pos", "competes_on": ["pos"],
            }), encoding="utf-8")
            (comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

            pos_results = tts.search_vendors("", competes_on_category="pos")
            self.assertEqual([r["id"] for r in pos_results], ["vendor-zeta-pos"])

            payments_results = tts.search_vendors("", competes_on_category="payments")
            self.assertEqual(payments_results, [])

            unfiltered = tts.search_vendors("")
            self.assertEqual(len(unfiltered), 1)  # the one fixture vendor, category filter off

    def test_tech_stack_read_excludes_editorial_content(self):
        """The core guarantee: risk and strategic_note (Todd's 2026-08-29
        exclude decision) must never appear in the response, even though
        they're present on the underlying relationship record."""
        result = tts.get_brand_tech_stack("brand-acme-burgers")
        self.assertEqual(len(result["vendor_relationships"]), 1)
        rel = result["vendor_relationships"][0]
        self.assertNotIn("risk", rel)
        self.assertNotIn("strategic_note", rel)
        # Sanity: the fixture really does carry that content, so this test
        # would fail loudly if the whitelist regressed to a passthrough.
        raw_graph = json.loads(self.graph_path.read_text())
        raw_rel = raw_graph["relationships"][0]
        self.assertEqual(raw_rel["risk"], "red")
        self.assertIn("Todd's private read", raw_rel["strategic_note"])
        # Factual fields still present.
        self.assertEqual(rel["vendor"], "Zeta POS")
        self.assertEqual(rel["category"], "pos")
        self.assertEqual(rel["deployment_stage"], "full_deployment")

    def test_tech_stack_read_unknown_brand_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_brand_tech_stack("brand-does-not-exist")

    def test_submit_rejects_unknown_brand(self):
        with self.assertRaises(tts.NotFoundError):
            tts.submit_tech_stack_entry(
                "brand-does-not-exist", "jsmith",
                vendor_id="vendor-zeta-pos", category="pos",
            )

    def test_submit_rejects_unknown_vendor(self):
        with self.assertRaises(tts.NotFoundError):
            tts.submit_tech_stack_entry(
                "brand-acme-burgers", "jsmith",
                vendor_id="vendor-does-not-exist", category="pos",
            )
        # No entity must have been created for the unknown vendor id.
        graph = json.loads(self.graph_path.read_text())
        self.assertFalse(any(e["id"] == "vendor-does-not-exist" for e in graph["entities"]))

    def test_canonical_account_fact_supersedes_conflicting_roster_row(self):
        entity = {
            "id": "brand-test", "name": "Test", "entity_type": "brand",
            "attributes": {"deep_research_profile": {"canonical_technology": {
                "POS": {"value": "Oracle — Oracle MICROS POS", "confidence": 0.95},
            }}},
        }
        graph = {"entities": [
            entity,
            {"id": "vendor-oracle", "name": "Oracle", "entity_type": "vendor"},
            {"id": "vendor-toast", "name": "Toast", "entity_type": "vendor"},
        ]}
        rows = [{
            "relationship_id": "rel-toast", "vendor": "Toast", "vendor_id": "vendor-toast",
            "category": "pos", "status": "active",
        }]

        reconciled, receipt = tts._reconcile_tech_stack_rows(entity, graph, rows)

        self.assertEqual([row["vendor"] for row in reconciled], ["Oracle"])
        self.assertEqual(reconciled[0]["roster_posture"], "canonical_current")
        self.assertEqual(receipt["suppressed"], 1)
        self.assertEqual(receipt["suppressed_relationships"][0]["relationship_id"], "rel-toast")

    def test_non_overlapping_graph_row_remains_supplemental(self):
        entity = {
            "id": "brand-test", "name": "Test", "entity_type": "brand",
            "attributes": {"deep_research_profile": {"canonical_technology": {
                "POS": {"value": "Oracle — Oracle MICROS POS", "confidence": 0.95},
            }}},
        }
        graph = {"entities": [entity, {"id": "vendor-olo", "name": "Olo", "entity_type": "vendor"}]}
        rows = [{
            "relationship_id": "rel-olo", "vendor": "Olo", "vendor_id": "vendor-olo",
            "category": "online_ordering", "status": "active",
        }]

        reconciled, receipt = tts._reconcile_tech_stack_rows(entity, graph, rows)

        olo = next(row for row in reconciled if row["vendor"] == "Olo")
        self.assertEqual(olo["roster_posture"], "supplemental_unreconciled")
        self.assertEqual(receipt["suppressed"], 0)

    def test_submit_new_entry_sets_attribution_and_persists_via_write_graph(self):
        result = tts.submit_tech_stack_entry(
            "brand-acme-burgers", "jsmith",
            vendor_id="vendor-zeta-pos", category="online_ordering",
            product="Zeta OLO", vendor_role="unknown",
        )
        self.assertTrue(result["added"])
        graph = json.loads(self.graph_path.read_text())
        new_rel = next(r for r in graph["relationships"] if r["id"] == result["relationship_id"])
        self.assertEqual(new_rel["last_modified_by"], "jsmith")
        self.assertEqual(new_rel["category"], "online_ordering")
        self.assertIn("src-team-jsmith", new_rel["sources"])
        # _write_graph always stamps last_updated on the whole graph —
        # confirms the real governed write path ran, not a raw json.dump
        # that would skip this.
        self.assertEqual(graph["last_updated"], tts.ei._today())
        # And a pre-write snapshot was taken (only happens inside
        # _write_graph, never in a raw write).
        self.assertTrue(any(self.snapshots_dir.glob("ecosystem_intelligence.pre-write-*.json")))

    def test_submit_updates_existing_relationship_when_relationship_id_given(self):
        result = tts.submit_tech_stack_entry(
            "brand-acme-burgers", "jsmith",
            relationship_id="rel-brand-acme-burgers-pos-system-of-record-pos-vendor-zeta-pos",
            vendor_id="vendor-zeta-pos", category="pos", product="Zeta Core v2",
        )
        self.assertFalse(result["added"])
        graph = json.loads(self.graph_path.read_text())
        rel = next(r for r in graph["relationships"] if r["id"] == result["relationship_id"])
        self.assertEqual(rel["product"], "Zeta Core v2")
        self.assertEqual(rel["last_modified_by"], "jsmith")
        # Original risk/strategic_note (Todd's editorial layer) is
        # untouched by a team edit that never even saw those fields.
        self.assertEqual(rel["risk"], "red")

    def test_written_graph_still_validates_against_schema(self):
        """Independent of _write_graph's own internal subprocess
        validation call (which — confirmed by reading system/schemas/
        validate.py — always validates the real production file, not a
        monkeypatched test path, since it resolves its target relative to
        its own file location rather than through core.
        ECOSYSTEM_INTELLIGENCE_PATH), directly validate the actual test
        output here so this test doesn't just trust an unrelated file."""
        tts.submit_tech_stack_entry(
            "brand-acme-burgers", "jsmith",
            vendor_id="vendor-zeta-pos", category="loyalty",
        )
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "validate_for_team_portal_test", ROOT / "system" / "schemas" / "validate.py")
        validate_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validate_mod)
        schema = json.loads((ROOT / "system" / "schemas" / "ecosystem_intelligence.schema.json").read_text())
        graph = json.loads(self.graph_path.read_text())
        errors = list(validate_mod.Validator(schema).iter_errors(graph))
        self.assertEqual(errors, [], f"schema violations: {errors}")


class TestBrandAndCompetitorProfile(unittest.TestCase):
    """Ecosystem Lookup Tool (Phase C, 2026-09-25): the new brand-profile
    and competitor-profile routes, plus the team-submission review queue.
    Same isolation pattern as TestTechStackWhitelist above, plus patching
    brand_profile_common.ROOT / competitor_intelligence_common.ROOT /
    team_profile_submissions.QUEUE_PATH so nothing here touches real data."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.graph_path = tmp / "ecosystem_intelligence.json"
        self.graph_path.write_text(json.dumps(_graph_fixture()), encoding="utf-8")
        self.snapshots_dir = tmp / "_snapshots"
        self._path_patch = patch.object(tts.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path)
        self._snap_patch = patch.object(tts.ei.core, "SNAPSHOTS_DIR", self.snapshots_dir)
        self._bpc_root_patch = patch.object(tts.bpc, "ROOT", tmp / "brand_profiles")
        self._cic_root_patch = patch.object(tts.cic, "ROOT", tmp / "competitor_intelligence")
        self._queue_patch = patch.object(tts.tps, "QUEUE_PATH", tmp / "team_profile_submissions.json")
        for p in (self._path_patch, self._snap_patch, self._bpc_root_patch,
                  self._cic_root_patch, self._queue_patch):
            p.start()

    def tearDown(self):
        for p in (self._path_patch, self._snap_patch, self._bpc_root_patch,
                  self._cic_root_patch, self._queue_patch):
            p.stop()
        self.tmpdir.cleanup()

    def test_get_brand_profile_returns_shareable_view_for_real_brand(self):
        profile = tts.get_brand_profile("brand-acme-burgers")
        self.assertEqual(profile["brand_id"], "brand-acme-burgers")
        self.assertIn("trajectory", profile)
        self.assertIn("footprint", profile)
        self.assertNotIn("reported_unverified", profile.get("leadership", {}))

    def test_get_brand_profile_unknown_brand_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_brand_profile("brand-does-not-exist")

    def test_get_competitor_extended_profile_excludes_internal_fields(self):
        # Seed real content on the competitor record via ensure_competitor's
        # own shell-creation path, then hand-populate a couple of fields.
        slug, _ = tts.compintel.ensure_competitor("Zeta POS")
        d = tts.cic.competitor_dir(slug)
        competitor = tts.cic.load_json(d / "competitor.json")
        competitor["strengths"] = [tts.cic.extended_field("Fast checkout")]
        competitor["vulnerabilities"] = [tts.cic.extended_field("Recent exec churn")]
        competitor["todds_pov"] = "Private read."
        tts.cic.save_json(d / "competitor.json", competitor)

        profile = tts.get_competitor_extended_profile("vendor-zeta-pos")
        self.assertEqual(profile["strengths"][0]["value"], "Fast checkout")
        self.assertNotIn("vulnerabilities", profile)
        self.assertNotIn("todds_pov", profile)

    def test_get_competitor_extended_profile_unknown_vendor_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_competitor_extended_profile("vendor-does-not-exist")

    def test_submit_profile_correction_rejects_unknown_brand(self):
        with self.assertRaises(tts.NotFoundError):
            tts.submit_profile_correction(
                target_type="brand", target_id="brand-does-not-exist",
                field_path="identity.hq_city_state", proposed_value="Chicago, IL",
                member_id="jsmith",
            )

    def test_submit_profile_correction_rejects_invalid_target_type(self):
        with self.assertRaises(tts.ValidationError):
            tts.submit_profile_correction(
                target_type="vendor", target_id="vendor-zeta-pos",
                field_path="strengths", proposed_value="x", member_id="jsmith",
            )

    def test_submit_profile_correction_queues_but_never_applies_live(self):
        submission = tts.submit_profile_correction(
            target_type="brand", target_id="brand-acme-burgers",
            field_path="identity.hq_city_state", proposed_value="Chicago, IL",
            member_id="jsmith",
        )
        self.assertEqual(submission["status"], "pending")
        # Not yet visible in the live profile.
        profile = tts.get_brand_profile("brand-acme-burgers")
        self.assertIsNone(profile["identity"]["hq_city_state"]["value"])

    def test_submit_then_confirm_makes_it_visible(self):
        submission = tts.submit_profile_correction(
            target_type="brand", target_id="brand-acme-burgers",
            field_path="identity.hq_city_state", proposed_value="Chicago, IL",
            member_id="jsmith",
        )
        tts.tps.confirm(submission["submission_id"], reviewed_by="todd")
        profile = tts.get_brand_profile("brand-acme-burgers")
        self.assertEqual(profile["identity"]["hq_city_state"]["value"], "Chicago, IL")


class TestMechanicalBrandBriefFallback(unittest.TestCase):
    """Ecosystem Lookup Tool follow-on (2026-09-25, Todd's "1,663 brands
    need a real mechanical brief, not a dead end" direction): get_brand_
    background_brief()'s fallback for the ~1,648 brands with no
    customers_prospects account -- sourced live from brand_profile_
    common.py's Phase A store + real ecosystem_intelligence.json
    relationships, never persisted (nothing to keep fresh; it's always
    current by construction). Same isolation pattern as
    TestBrandAndCompetitorProfile above."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.graph_path = tmp / "ecosystem_intelligence.json"
        self.graph_path.write_text(json.dumps(_graph_fixture()), encoding="utf-8")
        self._path_patch = patch.object(tts.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path)
        self._snap_patch = patch.object(tts.ei.core, "SNAPSHOTS_DIR", tmp / "_snapshots")
        self._bpc_root_patch = patch.object(tts.bpc, "ROOT", tmp / "brand_profiles")
        for p in (self._path_patch, self._snap_patch, self._bpc_root_patch):
            p.start()

    def tearDown(self):
        for p in (self._path_patch, self._snap_patch, self._bpc_root_patch):
            p.stop()
        self.tmpdir.cleanup()

    def test_brand_with_no_account_gets_a_real_mechanical_brief_not_a_dead_end(self):
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("Acme Burgers", result["markdown"])
        self.assertIn("Company Profile", result["markdown"])
        self.assertNotIn("No account research on file", result["markdown"])

    def test_mechanical_brief_includes_real_technology_environment_from_graph(self):
        """The fixture graph gives brand-acme-burgers a real active
        uses_vendor_for_category relationship to vendor-zeta-pos -- must
        show up in the Technology Environment table even with zero
        customers_prospects data."""
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("## Technology Environment", result["markdown"])
        self.assertIn("Zeta POS", result["markdown"])
        self.assertIn("pos", result["markdown"])

    def test_mechanical_brief_reflects_technomic_footprint_and_trajectory(self):
        # brand-acme-burgers' fixture attributes: unit_count 300, no
        # technomic_history -- footprint should render, trajectory should
        # honestly read insufficient_data (and therefore be omitted).
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("## Footprint", result["markdown"])
        self.assertIn("Total units: 300", result["markdown"])
        self.assertNotIn("## Trajectory", result["markdown"])

    def test_mechanical_brief_never_leaks_leadership_reported_unverified(self):
        profile = tts.bpc.empty_profile("brand-acme-burgers", "Acme Burgers")
        profile["leadership"]["confirmed"] = [{"name": "Jane Doe", "title": "CEO"}]
        profile["leadership"]["reported_unverified"] = [{"name": "Rumor Guy", "title": "maybe CFO?"}]
        tts.bpc.save_profile("brand-acme-burgers", profile)

        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("Jane Doe", result["markdown"])
        self.assertNotIn("Rumor Guy", result["markdown"])

    def test_mechanical_brief_shows_recent_signals(self):
        profile = tts.bpc.empty_profile("brand-acme-burgers", "Acme Burgers")
        tts.bpc.add_signal(profile, value="Opening 10 new units in Texas.",
                            signal_type="expansion_or_contraction", as_of="2026-09-01")
        tts.bpc.save_profile("brand-acme-burgers", profile)

        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("Recent Signals", result["markdown"])
        self.assertIn("Opening 10 new units in Texas.", result["markdown"])
        self.assertIn("2026-09-01", result["markdown"])

    def _set_deep_research_profile(self, brand_id: str, deep_research_profile: dict) -> None:
        graph = json.loads(self.graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == brand_id)
        entity.setdefault("attributes", {})["deep_research_profile"] = deep_research_profile
        self.graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def test_deep_research_leadership_roster_surfaces_in_mechanical_brief(self):
        """RB-2026-09-27: Todd noticed the Team Portal wasn't reflecting
        the intelligence the three deep-research ingest scripts had just
        written into ecosystem_intelligence.json's deep_research_profile
        namespace -- this is the closing test for that gap."""
        self._set_deep_research_profile("brand-acme-burgers", {
            "current_leadership": [
                {"name": "Jonathan Fitzpatrick", "title": "Chief Executive Officer", "confidence": 100},
            ],
        })
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("Leadership (Research Findings)", result["markdown"])
        self.assertIn("Jonathan Fitzpatrick", result["markdown"])
        self.assertIn("Chief Executive Officer", result["markdown"])

    def test_deep_research_canonical_technology_surfaces_in_mechanical_brief(self):
        self._set_deep_research_profile("brand-acme-burgers", {
            "canonical_technology": {"POS": {"value": "Oracle — Oracle MICROS POS", "confidence": 0.86}},
        })
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("Technology (Research Findings)", result["markdown"])
        self.assertIn("Oracle — Oracle MICROS POS", result["markdown"])

    def test_deep_research_franchise_disclosure_surfaces_in_mechanical_brief(self):
        self._set_deep_research_profile("brand-acme-burgers", {
            "franchise_disclosure": {"findings": {"value": {
                "pos": "SubwayPOS", "restaurant_technology_fee": "~$75/month",
            }}},
        })
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("## Franchise Disclosure", result["markdown"])
        self.assertIn("SubwayPOS", result["markdown"])
        self.assertIn("~$75/month", result["markdown"])

    def test_deep_research_evidence_findings_surface_and_are_capped(self):
        items = [{"finding": f"Finding number {i}.", "source_url": f"https://example.com/{i}"} for i in range(9)]
        self._set_deep_research_profile("brand-acme-burgers", {"deep_pass_public_evidence": items})
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("Public Evidence Findings", result["markdown"])
        self.assertIn("Finding number 0.", result["markdown"])
        self.assertIn("Finding number 5.", result["markdown"])
        self.assertNotIn("Finding number 6.", result["markdown"])
        self.assertIn("...and 3 more finding(s) on file.", result["markdown"])

    def test_missing_technology_source_citation_is_flagged_not_silent(self):
        """Todd, 2026-09-27: 'no fluff, nothing made up' -- the source
        dataset genuinely provides no per-category citation for
        canonical_technology (only a confidence score), and this must
        never be silently indistinguishable from a URL just not being
        rendered -- say so explicitly."""
        self._set_deep_research_profile("brand-acme-burgers", {
            "canonical_technology": {"POS": {"value": "Oracle MICROS", "confidence": 0.9}},
        })
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("No per-category source citation was provided", result["markdown"])

    def test_missing_scale_source_citation_is_flagged_not_silent(self):
        self._set_deep_research_profile("brand-acme-burgers", {
            "scale_snapshot": {"units": {"value": {"units": 300}, "confidence": 1, "as_of": "2026-05-27"}},
        })
        result = tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIn("No source citation was provided by the research pass for this snapshot", result["markdown"])
        self.assertIn("as of 2026-05-27", result["markdown"])

    def test_brand_with_no_deep_research_profile_shows_none_of_these_sections(self):
        result = tts.get_brand_background_brief("brand-acme-burgers")
        for heading in ("Leadership (Research Findings)", "Technology (Research Findings)",
                        "## Franchise Disclosure", "Public Evidence Findings"):
            self.assertNotIn(heading, result["markdown"])

    def test_unknown_brand_still_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_brand_background_brief("brand-does-not-exist")

    def test_mechanical_brief_is_never_persisted(self):
        """Read-only by design -- confirms no file gets written to
        brand_profiles/ just because someone read the mechanical brief."""
        tts.get_brand_background_brief("brand-acme-burgers")
        self.assertIsNone(tts.bpc.load_profile("brand-acme-burgers"))

    def test_email_button_works_for_a_brand_with_no_account(self):
        """The exact end-to-end path Todd's instruction is about: the
        canonical-background-brief/email button must work for ANY brand,
        not just the 15 formally engaged accounts."""
        result = tts.get_canonical_background_brief(
            "brand-acme-burgers", is_owner=False, requested_by="jsmith",
        )
        self.assertFalse(result["is_full_canonical"])
        self.assertIn("Acme Burgers", result["markdown"])
        self.assertNotIn("No account research on file", result["markdown"])


class TestCompetitiveBriefAndBattleCard(unittest.TestCase):
    """Ecosystem Lookup Tool follow-on (2026-09-25): "Competitive Brief" +
    "Battle Card" buttons on the vendor detail page. Both wrap the
    already-existing, already-versioned competitive_brief.py/battle_card.py
    artifacts (RB-2026-09-07) -- this confirms the is_owner generate-vs-
    read split and the battle card's inline Todd's-POV redaction (a
    different shape than the Competitor Profile's single removable '##
    Todd's POV' section, since one battle card embeds a POV line per
    competitor). Isolation pattern matches test_battle_card.py: disposable
    graph, competitor_intelligence.ROOT, artifact_vault.VAULT_ROOT, and
    intelligence_index paths -- competitive_brief.py and battle_card.py
    share the same artifact_vault_common/intelligence_index module objects,
    so patching them via tts.cbrief.avc/tts.cbrief.intelligence_index
    covers both."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.graph_path = tmp / "ecosystem_intelligence.json"
        self.graph_path.write_text(json.dumps(_graph_fixture()), encoding="utf-8")
        self._path_patch = patch.object(tts.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path)
        self._snap_patch = patch.object(tts.ei.core, "SNAPSHOTS_DIR", tmp / "_snapshots")
        self._cic_root_patch = patch.object(tts.cic, "ROOT", tmp / "competitor_intelligence")
        self._vault_patch = patch.object(tts.cbrief.avc, "VAULT_ROOT", tmp / "artifact_vault")
        self._ix_index_patch = patch.object(tts.cbrief.intelligence_index, "INDEX_PATH", tmp / "intelligence_index.json")
        self._ix_log_patch = patch.object(tts.cbrief.intelligence_index, "UPDATE_LOG_PATH", tmp / "intelligence_index_updates.jsonl")
        self._queue_patch = patch.object(tts.cbrq, "QUEUE_PATH", tmp / "competitive_brief_refresh_requests.jsonl")
        for p in (self._path_patch, self._snap_patch, self._cic_root_patch,
                  self._vault_patch, self._ix_index_patch, self._ix_log_patch, self._queue_patch):
            p.start()

    def tearDown(self):
        for p in (self._path_patch, self._snap_patch, self._cic_root_patch,
                  self._vault_patch, self._ix_index_patch, self._ix_log_patch, self._queue_patch):
            p.stop()
        self.tmpdir.cleanup()

    def _set_todds_pov(self, vendor_name: str, pov: str) -> str:
        slug, _created = tts.compintel.ensure_competitor(vendor_name)
        d = tts.cic.competitor_dir(slug)
        comp = tts.cic.load_json(d / "competitor.json")
        comp["todds_pov"] = pov
        tts.cic.save_json(d / "competitor.json", comp)
        return slug

    def test_competitive_brief_owner_generates_persisted_version(self):
        result = tts.get_competitive_brief_view("vendor-zeta-pos", is_owner=True, requested_by="todd")
        self.assertTrue(result["is_full_canonical"])
        self.assertIn("Zeta POS", result["markdown"])
        self.assertEqual(result["version"]["version"], 1)

    def test_competitive_brief_non_owner_before_generation_is_none(self):
        result = tts.get_competitive_brief_view("vendor-zeta-pos", is_owner=False, requested_by="jsmith")
        self.assertFalse(result["is_full_canonical"])
        self.assertIsNone(result["markdown"])

    def test_competitive_brief_never_shows_todds_pov(self):
        """RB-2026-09-28: Competitive Brief was redesigned to be a
        situational document (live accounts + recent evidence), distinct
        from Competitor Profile -- it never renders todds_pov at all now,
        for owner or teammate, since that content structurally isn't part
        of what this artifact renders (compare Competitor Profile's own
        '## Todd's POV' section, which still exists and is still
        redacted for teammates only)."""
        self._set_todds_pov("Zeta POS", "Private strategic read on Zeta.")
        owner_result = tts.get_competitive_brief_view("vendor-zeta-pos", is_owner=True, requested_by="todd")
        self.assertNotIn("Private strategic read on Zeta.", owner_result["markdown"])
        self.assertIn("Zeta POS", owner_result["markdown"])

        team_result = tts.get_competitive_brief_view("vendor-zeta-pos", is_owner=False, requested_by="jsmith")
        self.assertNotIn("Private strategic read on Zeta.", team_result["markdown"])
        self.assertIn("Zeta POS", team_result["markdown"])

    def test_competitive_brief_unknown_vendor_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_competitive_brief_view("vendor-does-not-exist", is_owner=True, requested_by="todd")

    def test_request_refresh_records_a_request_and_never_generates(self):
        """Todd's explicit "Option B" (2026-09-30): the request route must
        only record -- never call generate_competitive_brief/LLM
        synthesis itself. A non-owner teammate can use it too (unlike
        the owner-gated generate path above)."""
        with patch.object(tts.cbrief, "generate_competitive_brief") as mock_gen:
            result = tts.request_competitive_brief_refresh(
                "vendor-zeta-pos", member_id="jsmith", member_email="jane@example.com",
            )
        mock_gen.assert_not_called()
        self.assertEqual(result["vendor_id"], "vendor-zeta-pos")
        self.assertIn("requested_at", result)

        queued = [json.loads(l) for l in tts.cbrq.QUEUE_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["member_id"], "jsmith")
        self.assertEqual(queued[0]["email"], "jane@example.com")

    def test_request_refresh_unknown_vendor_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.request_competitive_brief_refresh(
                "vendor-does-not-exist", member_id="jsmith", member_email="jane@example.com",
            )

    def test_battle_card_owner_generates_persisted_version(self):
        result = tts.get_battle_card_view("vendor-zeta-pos", is_owner=True, requested_by="todd")
        self.assertTrue(result["available"])
        self.assertTrue(result["is_full_canonical"])
        self.assertEqual(result["category"], "pos")
        self.assertIn("Zeta POS", result["markdown"])

    def test_battle_card_non_owner_before_generation_is_none(self):
        result = tts.get_battle_card_view("vendor-zeta-pos", is_owner=False, requested_by="jsmith")
        self.assertTrue(result["available"])
        self.assertIsNone(result["markdown"])

    def test_battle_card_non_owner_pov_redacted(self):
        self._set_todds_pov("Zeta POS", "Zeta's renewal is at risk.")
        tts.get_battle_card_view("vendor-zeta-pos", is_owner=True, requested_by="todd")
        team_result = tts.get_battle_card_view("vendor-zeta-pos", is_owner=False, requested_by="jsmith")
        self.assertNotIn("Zeta's renewal is at risk.", team_result["markdown"])
        self.assertNotIn("Todd's POV", team_result["markdown"])
        self.assertIn("Zeta POS", team_result["markdown"])

    def test_battle_card_no_category_on_file_returns_unavailable(self):
        graph = _graph_fixture()
        graph["entities"].append({
            "id": "vendor-no-category", "name": "No Category Vendor", "entity_type": "vendor",
            "subtype": "restaurant_technology_vendor", "status": "active", "domains": ["restaurants"],
            "attributes": {}, "sources": ["src-technomic"], "confidence": {"level": "medium"},
        })
        self.graph_path.write_text(json.dumps(graph), encoding="utf-8")
        result = tts.get_battle_card_view("vendor-no-category", is_owner=True, requested_by="todd")
        self.assertFalse(result["available"])
        self.assertIsNone(result["markdown"])


class TestValueWedge(unittest.TestCase):
    """RB-2026-09-25, redesigned 2026-09-28: "Generate Value Wedge" action
    on the brand page's tech-stack table (brand+vendor scoped, the real
    three-circle methodology), same is_owner generate-vs-read split as
    Competitive Brief/Battle Card above -- but no redaction step, since
    nothing here is Todd-private. Same isolation pattern as
    TestCompetitiveBriefAndBattleCard, plus a disposable
    genius_capabilities.GENIUS_CAPABILITIES_PATH and a disposable
    brand_profile_common.ROOT."""

    BRAND_ID = "brand-acme-burgers"

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.graph_path = tmp / "ecosystem_intelligence.json"
        self.graph_path.write_text(json.dumps(_graph_fixture()), encoding="utf-8")
        self._path_patch = patch.object(tts.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path)
        self._snap_patch = patch.object(tts.ei.core, "SNAPSHOTS_DIR", tmp / "_snapshots")
        self._cic_root_patch = patch.object(tts.cic, "ROOT", tmp / "competitor_intelligence")
        self._vault_patch = patch.object(tts.vwedge.avc, "VAULT_ROOT", tmp / "artifact_vault")
        self._ix_index_patch = patch.object(tts.vwedge.intelligence_index, "INDEX_PATH", tmp / "intelligence_index.json")
        self._ix_log_patch = patch.object(tts.vwedge.intelligence_index, "UPDATE_LOG_PATH", tmp / "intelligence_index_updates.jsonl")
        self._gc_path_patch = patch.object(tts.vwedge.genius_capabilities, "GENIUS_CAPABILITIES_PATH", tmp / "genius_capabilities.json")
        self._bpc_root_patch = patch.object(tts.vwedge.bpc, "ROOT", tmp / "brand_profiles")
        for p in (self._path_patch, self._snap_patch, self._cic_root_patch, self._vault_patch,
                  self._ix_index_patch, self._ix_log_patch, self._gc_path_patch, self._bpc_root_patch):
            p.start()

    def tearDown(self):
        for p in (self._path_patch, self._snap_patch, self._cic_root_patch, self._vault_patch,
                  self._ix_index_patch, self._ix_log_patch, self._gc_path_patch, self._bpc_root_patch):
            p.stop()
        self.tmpdir.cleanup()

    def test_owner_generates_persisted_version(self):
        slug, _created = tts.compintel.ensure_competitor("Zeta POS")
        tts.compintel.set_competes_on(slug, ["pos"])
        tts.vwedge.genius_capabilities.add_capability("pos", "Real-time inventory sync across 40k locations")
        result = tts.get_value_wedge_view("vendor-zeta-pos", self.BRAND_ID, is_owner=True, requested_by="todd")
        self.assertTrue(result["is_full_canonical"])
        self.assertEqual(result["data"]["brand_id"], self.BRAND_ID)
        points = [c["point"] for c in result["data"]["circle1_genius_strengths"]]
        self.assertIn("Real-time inventory sync across 40k locations", points)
        self.assertEqual(result["version"]["version"], 1)

    def test_non_owner_before_generation_is_none(self):
        result = tts.get_value_wedge_view("vendor-zeta-pos", self.BRAND_ID, is_owner=False, requested_by="jsmith")
        self.assertFalse(result["is_full_canonical"])
        self.assertIsNone(result["data"])

    def test_non_owner_reads_current_version_unredacted(self):
        """No POV to redact -- a teammate should see exactly what Todd
        generated, verbatim."""
        slug, _created = tts.compintel.ensure_competitor("Zeta POS")
        tts.compintel.set_competes_on(slug, ["pos"])
        tts.vwedge.genius_capabilities.add_capability("pos", "Real-time inventory sync")
        owner_result = tts.get_value_wedge_view("vendor-zeta-pos", self.BRAND_ID, is_owner=True, requested_by="todd")
        team_result = tts.get_value_wedge_view("vendor-zeta-pos", self.BRAND_ID, is_owner=False, requested_by="jsmith")
        self.assertEqual(owner_result["data"], team_result["data"])

    def test_unknown_vendor_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_value_wedge_view("vendor-does-not-exist", self.BRAND_ID, is_owner=True, requested_by="todd")

    def test_unknown_brand_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_value_wedge_view("vendor-zeta-pos", "brand-does-not-exist", is_owner=True, requested_by="todd")

    def test_battle_card_unknown_vendor_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_battle_card_view("vendor-does-not-exist", is_owner=True, requested_by="todd")

    def test_redact_battle_card_for_team_strips_and_rewords(self):
        markdown = (
            "# POS — Battle Card\n"
            "\nGenius: 1 brands (100.0% of known) out of 1/1 brands tracked.\n"
            "\n## Competitors\n"
            "\n### Zeta POS\n"
            "\nZeta POS: 1 brand (100.0% of known)\n"
            "\n**Positioning:** Public positioning text.\n"
            "\n**Todd's POV:** Private strategic read.\n"
            "\n**Genius advantages:**\n- Genius wins on X.\n"
            "\n**RM posture:** Lead with restaurant depth.\n"
            "\n**When to bring Todd in:** When they cite Burger King rollout scale.\n"
        )
        redacted = tts._redact_battle_card_for_team(markdown)
        self.assertNotIn("Todd's POV", redacted)
        self.assertNotIn("Private strategic read", redacted)
        self.assertIn("Public positioning text", redacted)
        self.assertIn("Genius wins on X.", redacted)
        self.assertNotIn("RM posture", redacted)
        self.assertIn("**Team posture:** Lead with restaurant depth.", redacted)
        self.assertNotIn("When to bring Todd in", redacted)
        self.assertIn("**Escalation trigger:** When they cite Burger King rollout scale.", redacted)


class TestCompetitorProfileRedaction(unittest.TestCase):
    """Pure string-manipulation logic — no filesystem/network needed. Only
    the Competitor Profile still uses this blocklist approach (Todd
    confirmed "Positioning"/"Gap analysis vs. Genius" are fine to show as-
    is — see TestTeamSafeBackgroundBrief below for why the Background
    Brief moved to an allowlist instead)."""

    def test_redacts_competitor_pov_section(self):
        markdown = (
            "# Zeta POS — Competitor Intelligence Profile\n"
            "\n## Positioning\nPublic positioning text.\n"
            "\n## Todd's POV\nTodd's private strategic read on Zeta.\n"
            "\n## Gap analysis vs. Genius\n- Genius wins: tighter integration.\n"
        )
        redacted = tts._redact_markdown_section(markdown, tts._COMPETITOR_POV_SECTION_HEADING)
        self.assertNotIn("Todd's POV", redacted)
        self.assertNotIn("private strategic read", redacted)
        self.assertIn("Public positioning text", redacted)
        self.assertIn("Gap analysis vs. Genius", redacted)

    def test_missing_section_is_a_no_op(self):
        markdown = "# Profile\n\n## Positioning\nText.\n"
        self.assertEqual(tts._redact_markdown_section(markdown, tts._COMPETITOR_POV_SECTION_HEADING), markdown)

    def test_section_as_last_block_still_fully_removed(self):
        markdown = "# Profile\n\n## Todd's POV\nSecret stuff.\n"
        redacted = tts._redact_markdown_section(markdown, tts._COMPETITOR_POV_SECTION_HEADING)
        self.assertNotIn("Secret stuff", redacted)
        self.assertNotIn("Todd's POV", redacted)


TEST_BRIEF_DISPLAY_NAME = "Test Fixture Team Portal Brand"
TEST_BRIEF_SLUG = "test-fixture-team-portal-brand"  # must match ei._slug(TEST_BRIEF_DISPLAY_NAME) — resolve_account() derives this from the brand entity's name


class TestTeamSafeBackgroundBrief(unittest.TestCase):
    """RB-2026-08-29: live-tested against a real McDonald's brief through
    the actual UI and found the original design (redact one known
    section) let real content through -- Todd's sales strategy, named
    colleagues ("Ask David Lee...", "Ask Jeff Coffland..."), and an
    explicit instruction to keep competitor employees out of internal
    discussions, all via sections (Bottom Line, Recommended Approach, a
    dynamic per-field free-text loop) the blocklist never anticipated.
    Fixed by switching to an allowlist: team_tech_stack.get_brand_
    background_brief() assembles ONLY four structural sections via
    account_background_brief.py's extracted per-section renderers, and
    never touches render_background_brief()/generate_brief() at all — so
    a new free-text field added to that pipeline in the future can't
    silently reach the team's view the way "Bottom Line" did.

    Real filesystem I/O against a disposable account_research fixture
    directory (same pattern as test_account_background_brief.py's
    TestOrchestratorWithIsolatedFixtureAccount) plus a matching brand
    entity in a monkeypatched ecosystem_intelligence.json, since
    get_brand_background_brief resolves the brand through the graph first."""

    def setUp(self):
        import tempfile
        self.account_dir_path = cpc.ROOT / "accounts" / TEST_BRIEF_SLUG
        if self.account_dir_path.exists():
            shutil.rmtree(self.account_dir_path)
        self.account_dir_path.mkdir(parents=True)
        cpc.save_json(self.account_dir_path / "account.json", {
            "account_id": f"acct-{TEST_BRIEF_SLUG}", "account_slug": TEST_BRIEF_SLUG,
            "display_name": "Test Fixture Team Portal Brand", "aliases": [],
            "portfolio_status": {"value": "research", "status": "confirmed", "evidence_ids": [],
                                  "confidence": "high", "as_of": "2026-08-29", "scope": "account",
                                  "last_reviewed_by": "human:test"},
            "owners": [],
            "executive_summary": "Todd's private strategic read: this is a soft target.",
            "current_business_situation": "Todd's private situational analysis.",
            "leadership": {
                "confirmed": [{"name": "Jane Confirmed", "title": "CEO"}],
                "reported_unverified": [{"name": "Sam Unverified", "title": "CTO"}],
            },
            "technology_stack": [{
                "layer": "POS", "vendor": "RealVendor", "status": "Verify", "confidence": "medium",
                "current_state": "seed", "evidence_ids": [], "as_of": "2026-08-01",
                "scope": "account", "last_reviewed_by": "human:test",
            }],
            "commercial_models": [], "buying_influences": [], "opportunities": [],
            "qualification": {"criteria": []}, "strategic_position": {},
            "bottom_line": "Todd's private sales strategy: ask Jane Colleague to review the pitch before we approach.",
            "recommended_approach": "Todd's private recommended approach: lead with the loyalty angle.",
            "latest_review": {}, "template_version": "test", "updated_at": "2026-08-29T00:00:00Z",
        })
        cpc.save_json(self.account_dir_path / "brand_profile.json", {
            "account_id": f"acct-{TEST_BRIEF_SLUG}",
            "founded": "1999", "headquarters": "Testville, TX", "segment": "LSR",
            "footprint": "200 locations", "ownership": "Private", "geography": "Southwest US",
            "differentiation": "Fast, fresh, friendly.", "competitive_set": "Rival Burgers, Copycat Grill",
            "current_business_situation": "Todd's private brand-level situation notes.",
            # Exactly the shape that leaked live: a free-text field not in
            # the core allowlist, dynamically titled and rendered by
            # render_background_brief()'s per-field loop.
            "why_loyalty_matters": "Todd's private strategic read on why loyalty is the wedge here.",
        })
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{TEST_BRIEF_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{TEST_BRIEF_SLUG}", "sources": []})
        (self.account_dir_path / "evidence.jsonl").write_text("", encoding="utf-8")

        # Matching brand entity, monkeypatched graph (same convention as
        # TestTechStackWhitelist above).
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.graph_path = tmp / "ecosystem_intelligence.json"
        self.graph_path.write_text(json.dumps({
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-08-29",
            "domain_packs": ["restaurants"],
            "entities": [{
                "id": "brand-test-fixture-team-portal", "name": "Test Fixture Team Portal Brand",
                "entity_type": "brand", "subtype": "restaurant_brand", "status": "active",
                "domains": ["restaurants"],
                "attributes": {"deep_research_profile": {
                    "canonical_technology": {"POS": {"value": "Deep-Research Sourced POS", "confidence": 0.9}},
                }},
                "sources": [], "confidence": {"level": "medium"},
            }],
            "relationships": [], "assessments": [], "signals": [], "sources": [],
            "user_relevance": [], "strategic_recommendations": [],
        }), encoding="utf-8")
        self._path_patch = patch.object(tts.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path)
        self._path_patch.start()

    def tearDown(self):
        self._path_patch.stop()
        self.tmpdir.cleanup()
        if self.account_dir_path.exists():
            shutil.rmtree(self.account_dir_path)

    def test_structural_facts_present(self):
        brief = tts.get_brand_background_brief("brand-test-fixture-team-portal")
        md = brief["markdown"]
        self.assertIn("Founded: 1999", md)
        self.assertIn("Headquarters: Testville, TX", md)
        self.assertIn("RealVendor", md)  # Technology Environment table
        self.assertIn("Jane Confirmed", md)  # Leadership
        self.assertIn("Sam Unverified", md)

    def test_deep_research_intelligence_supplements_the_dossier(self):
        """RB-2026-09-27: an account WITH a customers_prospects dossier
        still benefits from the ecosystem-wide deep-research namespace --
        it's a supplement to Todd's own dossier facts, not a replacement,
        and both can appear together safely since neither is his personal
        strategy content."""
        brief = tts.get_brand_background_brief("brand-test-fixture-team-portal")
        md = brief["markdown"]
        self.assertIn("Technology (Research Findings)", md)
        self.assertIn("Deep-Research Sourced POS", md)

    def test_strategic_content_never_appears(self):
        """The exact class of leak found live: Bottom Line, Recommended
        Approach, Executive Summary, Current Business Situation, and any
        dynamic free-text field, all excluded -- not just the one section
        the old blocklist targeted."""
        brief = tts.get_brand_background_brief("brand-test-fixture-team-portal")
        md = brief["markdown"]
        self.assertNotIn("soft target", md)  # executive_summary
        self.assertNotIn("situational analysis", md)  # current_business_situation
        self.assertNotIn("sales strategy", md)  # bottom_line
        self.assertNotIn("Jane Colleague", md)  # named colleague inside bottom_line
        self.assertNotIn("loyalty angle", md)  # recommended_approach
        self.assertNotIn("why loyalty is the wedge", md)  # dynamic why_loyalty_matters field
        self.assertNotIn("Bottom Line", md)
        self.assertNotIn("Recommended Approach", md)
        self.assertNotIn("Executive Summary", md)
        self.assertNotIn("Current Business Situation", md)
        self.assertNotIn("Why Loyalty Matters", md)

    def test_never_calls_the_full_brief_pipeline(self):
        """Structural guarantee, not just content-based: this function
        must not invoke generate_brief/render_background_brief at all, so
        a future change to that pipeline can't reopen this leak."""
        with patch.object(abb, "generate_brief") as mock_generate, \
             patch.object(abb, "render_background_brief") as mock_render:
            tts.get_brand_background_brief("brand-test-fixture-team-portal")
            mock_generate.assert_not_called()
            mock_render.assert_not_called()

    def test_never_writes_or_versions_anything(self):
        """A teammate's read must not create/version an Account Research
        brief as a side effect -- that pipeline belongs to Todd alone."""
        briefs_dir = self.account_dir_path / "briefs"
        self.assertFalse(briefs_dir.exists())
        tts.get_brand_background_brief("brand-test-fixture-team-portal")
        self.assertFalse(briefs_dir.exists())

    def test_unknown_brand_raises_not_found(self):
        with self.assertRaises(tts.NotFoundError):
            tts.get_brand_background_brief("brand-does-not-exist")

    def test_brand_with_no_account_research_dossier_returns_gracefully(self):
        """No customers_prospects dossier for this brand at all -- must
        not crash and must not auto-create one (that would be a write
        triggered by a read). 2026-09-25: no longer a dead end -- falls
        back to the mechanical brief (_render_mechanical_brand_brief),
        which for a brand with truly empty attributes/no relationships
        renders its own honest "no research on file" blank -- still never
        touching customers_prospects/ at all."""
        graph = json.loads(self.graph_path.read_text())
        graph["entities"].append({
            "id": "brand-no-dossier-fixture", "name": "Brand With No Dossier At All XYZ",
            "entity_type": "brand", "subtype": "restaurant_brand", "status": "active",
            "domains": ["restaurants"], "attributes": {}, "sources": [], "confidence": {"level": "medium"},
        })
        self.graph_path.write_text(json.dumps(graph), encoding="utf-8")
        no_dossier_dir = cpc.ROOT / "accounts" / "brand-no-dossier-fixture-xyz"
        self.assertFalse(no_dossier_dir.exists())
        brief = tts.get_brand_background_brief("brand-no-dossier-fixture")
        self.assertIn("No research on file for this brand yet", brief["markdown"])
        self.assertFalse(no_dossier_dir.exists())


@unittest.skipUnless(_FASTAPI_OK, "fastapi not installed")
class TestAuth(unittest.TestCase):
    """FastAPI-level: confirms the auth dependency actually gates every
    /api/* route, with no shared-secret fallback (unlike rbb_chat.py's
    optional-passcode design, appropriate there for a single-user tool but
    wrong here), and that revocation is immediate."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.manifest_path = tmp / "manifest.yaml"
        self.creds_path = tmp / "creds.json"

        import hashlib
        self.token = "test-token-abc123"
        token_hash = hashlib.sha256(self.token.encode()).hexdigest()
        self.creds_path.write_text(json.dumps({
            "jsmith": {"token_hash": token_hash, "created_at": "2026-08-29T00:00:00+00:00", "revoked_at": None},
        }))
        import yaml
        self.manifest_path.write_text(yaml.safe_dump({
            "members": [{"id": "jsmith", "name": "Jane Smith", "email": "jane@example.com",
                         "role": "team_member", "added_at": "2026-08-29T00:00:00+00:00", "revoked_at": None}],
        }))

        self._manifest_patch = patch.object(team_portal_api, "MANIFEST_PATH", self.manifest_path)
        self._creds_patch = patch.object(team_portal_api, "CREDENTIALS_PATH", self.creds_path)
        self._manifest_patch.start()
        self._creds_patch.start()

        graph_path = tmp / "ecosystem_intelligence.json"
        graph_path.write_text(json.dumps(_graph_fixture()))
        self._graph_path_patch = patch.object(tts.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
        self._graph_path_patch.start()

        self.client = TestClient(team_portal_api.app)

    def tearDown(self):
        self._manifest_patch.stop()
        self._creds_patch.stop()
        self._graph_path_patch.stop()
        self.tmpdir.cleanup()

    def test_no_auth_header_rejected(self):
        resp = self.client.get("/api/brands/search")
        self.assertEqual(resp.status_code, 401)

    def test_wrong_token_rejected(self):
        resp = self.client.get("/api/brands/search", headers={"Authorization": "Bearer not-a-real-token"})
        self.assertEqual(resp.status_code, 401)

    def test_malformed_header_rejected(self):
        resp = self.client.get("/api/brands/search", headers={"Authorization": self.token})  # missing "Bearer "
        self.assertEqual(resp.status_code, 401)

    def test_valid_token_accepted(self):
        resp = self.client.get("/api/brands/search", headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["brands"][0]["id"], "brand-acme-burgers")

    def test_revoked_token_immediately_rejected(self):
        # Confirm valid first.
        resp = self.client.get("/api/brands/search", headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(resp.status_code, 200)
        # Revoke, no server restart.
        creds = json.loads(self.creds_path.read_text())
        creds["jsmith"]["revoked_at"] = "2026-08-29T01:00:00+00:00"
        self.creds_path.write_text(json.dumps(creds))
        resp = self.client.get("/api/brands/search", headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(resp.status_code, 401)

    def test_unknown_brand_id_is_404_via_the_route(self):
        resp = self.client.get(
            "/api/brands/brand-does-not-exist/tech-stack",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_tech_stack_response_excludes_editorial_fields_end_to_end(self):
        resp = self.client.get(
            "/api/brands/brand-acme-burgers/tech-stack",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 200)
        rel = resp.json()["vendor_relationships"][0]
        self.assertNotIn("risk", rel)
        self.assertNotIn("strategic_note", rel)

    def test_post_tech_stack_unknown_vendor_returns_422_or_404_not_500(self):
        resp = self.client.post(
            "/api/brands/brand-acme-burgers/tech-stack",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"vendor_id": "vendor-does-not-exist", "category": "pos"},
        )
        self.assertEqual(resp.status_code, 404)


class TestEcosystemLookupRoutes(unittest.TestCase):
    """Route-level (FastAPI TestClient) coverage for the 2026-09-25
    Ecosystem Lookup Tool additions: brand/competitor profile routes, the
    team-submission queue route, and the canonical-background-brief +
    email routes -- including the is_owner routing split and Team Portal's
    isolated SMTP credential. Same auth-fixture pattern as TestAuth, plus
    the bpc.ROOT/cic.ROOT/tps.QUEUE_PATH isolation from
    TestBrandAndCompetitorProfile. The real account_background_brief /
    customers_prospects_common pipeline the is_owner=True branch calls is
    already covered by test_account_background_brief.py -- here we only
    confirm this route reaches it with the right arguments, not re-test
    its internals."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)

        import hashlib
        self.token = "test-token-jsmith"
        self.owner_token = "test-token-todd"
        self.creds_path = tmp / "creds.json"
        self.creds_path.write_text(json.dumps({
            "jsmith": {"token_hash": hashlib.sha256(self.token.encode()).hexdigest(),
                       "created_at": "2026-09-25T00:00:00+00:00", "revoked_at": None},
            "todd": {"token_hash": hashlib.sha256(self.owner_token.encode()).hexdigest(),
                     "created_at": "2026-09-25T00:00:00+00:00", "revoked_at": None},
        }))
        import yaml
        self.manifest_path = tmp / "manifest.yaml"
        self.manifest_path.write_text(yaml.safe_dump({
            "members": [
                {"id": "jsmith", "name": "Jane Smith", "added_at": "2026-09-25T00:00:00+00:00", "revoked_at": None},
                {"id": "todd", "name": "Todd Vahlsing", "is_owner": True,
                 "added_at": "2026-09-25T00:00:00+00:00", "revoked_at": None},
            ],
        }))
        self._manifest_patch = patch.object(team_portal_api, "MANIFEST_PATH", self.manifest_path)
        self._creds_patch = patch.object(team_portal_api, "CREDENTIALS_PATH", self.creds_path)
        self._manifest_patch.start()
        self._creds_patch.start()

        graph_path = tmp / "ecosystem_intelligence.json"
        graph_path.write_text(json.dumps(_graph_fixture()))
        self._graph_path_patch = patch.object(tts.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
        self._snap_patch = patch.object(tts.ei.core, "SNAPSHOTS_DIR", tmp / "_snapshots")
        self._bpc_root_patch = patch.object(tts.bpc, "ROOT", tmp / "brand_profiles")
        self._cic_root_patch = patch.object(tts.cic, "ROOT", tmp / "competitor_intelligence")
        self._queue_patch = patch.object(tts.tps, "QUEUE_PATH", tmp / "team_profile_submissions.json")
        self._vault_patch = patch.object(tts.cbrief.avc, "VAULT_ROOT", tmp / "artifact_vault")
        self._ix_index_patch = patch.object(tts.cbrief.intelligence_index, "INDEX_PATH", tmp / "intelligence_index.json")
        self._ix_log_patch = patch.object(tts.cbrief.intelligence_index, "UPDATE_LOG_PATH", tmp / "intelligence_index_updates.jsonl")
        self._refresh_queue_patch = patch.object(tts.cbrq, "QUEUE_PATH", tmp / "competitive_brief_refresh_requests.jsonl")
        for p in (self._graph_path_patch, self._snap_patch, self._bpc_root_patch,
                  self._cic_root_patch, self._queue_patch, self._vault_patch,
                  self._ix_index_patch, self._ix_log_patch, self._refresh_queue_patch):
            p.start()

        self.client = TestClient(team_portal_api.app)

    def tearDown(self):
        for p in (self._manifest_patch, self._creds_patch, self._graph_path_patch,
                  self._snap_patch, self._bpc_root_patch, self._cic_root_patch, self._queue_patch,
                  self._vault_patch, self._ix_index_patch, self._ix_log_patch, self._refresh_queue_patch):
            p.stop()
        self.tmpdir.cleanup()

    def _auth(self, owner: bool = False) -> dict:
        return {"Authorization": f"Bearer {self.owner_token if owner else self.token}"}

    def test_get_brand_ecosystem_profile_route(self):
        resp = self.client.get("/api/brands/brand-acme-burgers/ecosystem-profile", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["brand_id"], "brand-acme-burgers")
        self.assertIn("trajectory", body)

    def test_get_brand_ecosystem_profile_unknown_brand_404(self):
        resp = self.client.get("/api/brands/brand-does-not-exist/ecosystem-profile", headers=self._auth())
        self.assertEqual(resp.status_code, 404)

    def test_get_competitor_extended_profile_route(self):
        resp = self.client.get(
            "/api/vendors/vendor-zeta-pos/competitor-extended-profile", headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["vendor_id"], "vendor-zeta-pos")

    def test_get_competitor_extended_profile_unknown_vendor_404(self):
        resp = self.client.get(
            "/api/vendors/vendor-does-not-exist/competitor-extended-profile", headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_post_profile_submission_route(self):
        resp = self.client.post(
            "/api/profile-submissions", headers=self._auth(),
            json={"target_type": "brand", "target_id": "brand-acme-burgers",
                  "field_path": "identity.hq_city_state", "proposed_value": "Chicago, IL"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pending")

    def test_post_profile_submission_unknown_brand_404(self):
        resp = self.client.post(
            "/api/profile-submissions", headers=self._auth(),
            json={"target_type": "brand", "target_id": "brand-does-not-exist",
                  "field_path": "identity.hq_city_state", "proposed_value": "Chicago, IL"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_post_profile_submission_invalid_target_type_422(self):
        resp = self.client.post(
            "/api/profile-submissions", headers=self._auth(),
            json={"target_type": "vendor", "target_id": "vendor-zeta-pos",
                  "field_path": "strengths", "proposed_value": "x"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_canonical_background_brief_non_owner_gets_redacted_variant(self):
        resp = self.client.post(
            "/api/brands/brand-acme-burgers/canonical-background-brief", headers=self._auth(owner=False),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["is_full_canonical"])

    def test_canonical_background_brief_owner_routes_to_full_pipeline(self):
        with patch.object(tts.abb, "generate_brief", return_value={"markdown": "# Full Brief", "version": 1}) as mock_gen, \
             patch.object(tts.abb, "resolve_account", return_value=("brand-acme-burgers", False)), \
             patch.object(tts.cpc, "create_pre_engagement_shell") as mock_shell:
            resp = self.client.post(
                "/api/brands/brand-acme-burgers/canonical-background-brief", headers=self._auth(owner=True),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["is_full_canonical"])
        self.assertEqual(body["markdown"], "# Full Brief")
        mock_shell.assert_called_once()
        mock_gen.assert_called_once()
        self.assertEqual(mock_gen.call_args.kwargs.get("generated_for"), "todd")

    def test_canonical_background_brief_unknown_brand_404(self):
        resp = self.client.post(
            "/api/brands/brand-does-not-exist/canonical-background-brief", headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_email_canonical_background_brief_not_configured_returns_503(self):
        with patch.object(tpe, "resolve_smtp", return_value=None):
            resp = self.client.post(
                "/api/brands/brand-acme-burgers/canonical-background-brief/email", headers=self._auth(),
                json={"recipient": "teammate@example.com"},
            )
        self.assertEqual(resp.status_code, 503)

    def test_email_canonical_background_brief_success(self):
        with patch.object(tpe, "send_brief", return_value={"sent": True, "status": "smtp_sent",
                                                             "recipient": "teammate@example.com", "subject": "s"}) as mock_send:
            resp = self.client.post(
                "/api/brands/brand-acme-burgers/canonical-background-brief/email", headers=self._auth(),
                json={"recipient": "teammate@example.com"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["sent"])
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs.get("recipient"), "teammate@example.com")

    def test_email_canonical_background_brief_smtp_failure_returns_502(self):
        with patch.object(tpe, "send_brief", return_value={"sent": False, "status": "smtp_error", "detail": "connection refused"}):
            resp = self.client.post(
                "/api/brands/brand-acme-burgers/canonical-background-brief/email", headers=self._auth(),
                json={"recipient": "teammate@example.com"},
            )
        self.assertEqual(resp.status_code, 502)

    def test_email_canonical_background_brief_unknown_brand_404(self):
        resp = self.client.post(
            "/api/brands/brand-does-not-exist/canonical-background-brief/email", headers=self._auth(),
            json={"recipient": "teammate@example.com"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_email_report_generic_success(self):
        """2026-09-28: the generic 'email any report' endpoint -- caller
        supplies subject+markdown it already has in hand (Competitor
        Profile, Competitive Brief, Battle Card, Value Wedge all use this
        same route), no re-fetch/re-generate happens server-side."""
        with patch.object(tpe, "send_brief", return_value={"sent": True, "status": "smtp_sent",
                                                             "recipient": "teammate@example.com", "subject": "s"}) as mock_send:
            resp = self.client.post(
                "/api/email-report", headers=self._auth(),
                json={"recipient": "teammate@example.com", "subject": "PAR Technology — Battle Card", "markdown": "# real content"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["sent"])
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs.get("markdown_body"), "# real content")
        self.assertEqual(mock_send.call_args.kwargs.get("subject"), "PAR Technology — Battle Card")

    def test_email_report_generic_not_configured_returns_503(self):
        with patch.object(tpe, "resolve_smtp", return_value=None):
            resp = self.client.post(
                "/api/email-report", headers=self._auth(),
                json={"recipient": "teammate@example.com", "subject": "s", "markdown": "m"},
            )
        self.assertEqual(resp.status_code, 503)

    def test_email_report_generic_smtp_failure_returns_502(self):
        with patch.object(tpe, "send_brief", return_value={"sent": False, "status": "smtp_error", "detail": "connection refused"}):
            resp = self.client.post(
                "/api/email-report", headers=self._auth(),
                json={"recipient": "teammate@example.com", "subject": "s", "markdown": "m"},
            )
        self.assertEqual(resp.status_code, 502)

    def test_competitive_brief_route_owner_generates_then_teammate_reads(self):
        owner_resp = self.client.post("/api/vendors/vendor-zeta-pos/competitive-brief", headers=self._auth(owner=True))
        self.assertEqual(owner_resp.status_code, 200)
        self.assertTrue(owner_resp.json()["is_full_canonical"])

        team_resp = self.client.post("/api/vendors/vendor-zeta-pos/competitive-brief", headers=self._auth(owner=False))
        self.assertEqual(team_resp.status_code, 200)
        self.assertFalse(team_resp.json()["is_full_canonical"])
        self.assertIn("Zeta POS", team_resp.json()["markdown"])

    def test_competitive_brief_route_unknown_vendor_404(self):
        resp = self.client.post("/api/vendors/vendor-does-not-exist/competitive-brief", headers=self._auth(owner=True))
        self.assertEqual(resp.status_code, 404)

    def test_competitive_brief_request_refresh_route_available_to_non_owner(self):
        """Todd's "Option B" (2026-09-30): unlike the owner-gated generate
        route above, any teammate can request a refresh."""
        with patch.object(tts.cbrief, "generate_competitive_brief") as mock_gen:
            resp = self.client.post(
                "/api/vendors/vendor-zeta-pos/competitive-brief/request-refresh",
                headers=self._auth(owner=False),
            )
        self.assertEqual(resp.status_code, 200)
        mock_gen.assert_not_called()
        body = resp.json()
        self.assertEqual(body["vendor_id"], "vendor-zeta-pos")
        self.assertIn("requested_at", body)

    def test_competitive_brief_request_refresh_route_unknown_vendor_404(self):
        resp = self.client.post(
            "/api/vendors/vendor-does-not-exist/competitive-brief/request-refresh",
            headers=self._auth(owner=False),
        )
        self.assertEqual(resp.status_code, 404)

    def test_battle_card_route_owner_generates_then_teammate_reads(self):
        owner_resp = self.client.post("/api/vendors/vendor-zeta-pos/battle-card", headers=self._auth(owner=True))
        self.assertEqual(owner_resp.status_code, 200)
        body = owner_resp.json()
        self.assertTrue(body["available"])
        self.assertTrue(body["is_full_canonical"])
        self.assertEqual(body["category"], "pos")

        team_resp = self.client.post("/api/vendors/vendor-zeta-pos/battle-card", headers=self._auth(owner=False))
        self.assertEqual(team_resp.status_code, 200)
        self.assertFalse(team_resp.json()["is_full_canonical"])

    def test_battle_card_route_unknown_vendor_404(self):
        resp = self.client.post("/api/vendors/vendor-does-not-exist/battle-card", headers=self._auth(owner=True))
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
