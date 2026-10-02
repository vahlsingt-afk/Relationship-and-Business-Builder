#!/usr/bin/env python3
"""
test_account_background_brief.py — RB-2026-08-27.

account_research_common.py has no path-injection mechanism (ROOT is a
module-level constant resolved at import time, unlike ecosystem_intelligence.py's
explicit-path pattern) — this is a real constraint discovered while writing
these tests, not an oversight. So isolation here is a genuinely separate,
disposable account directory under the real system/account_research/accounts/
(created and deleted by each test that needs one), never a monkey-patched
path and never a mutation of the shared portfolio registry. Pure-logic
tests (no filesystem) are preferred wherever the function allows it.

2026-08-27, corrected same day as the original build: Account Background
Brief moved out of blue_sheets/ entirely (it's a pre-engagement document,
upstream of a Blue Sheet, and must not require one to exist) into its own
system/account_research/ tree. TestHistoricalPreservationOnChange below
still exercises blue_sheets/_engine/impact_review.py directly -- that fix
(never discard a superseded field-object value) is real Blue Sheet engine
behavior, unrelated to the storage-location move, and stays covered here
since this file already had the fixture for it.
"""
from __future__ import annotations

import shutil
import sys
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "blue_sheets" / "_engine"))

import ecosystem_intelligence as ei  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import impact_review as ir  # noqa: E402
import account_background_brief as abb  # noqa: E402

TEST_SLUG = "test-fixture-account-background-brief"


class TestDiacriticNormalization(unittest.TestCase):
    """The real bug found during this feature's own research: 'Café Rio'
    never matched the existing 'Cafe Rio Mexican Grill' entity because the
    old normalization treated accented letters as separators."""

    def test_norm_key_strips_diacritics(self):
        self.assertEqual(ei._norm_key("Café Rio"), "cafe_rio")

    def test_norm_key_matches_unaccented_spelling(self):
        accented = set(ei._norm_key("Café Rio").split("_"))
        unaccented = set(ei._norm_key("Cafe Rio Mexican Grill").split("_"))
        self.assertTrue(accented <= unaccented, f"{accented} not a subset of {unaccented}")

    def test_slug_strips_diacritics(self):
        self.assertEqual(ei._slug("Café Rio"), "cafe-rio")

    def test_ascii_names_unaffected(self):
        self.assertEqual(ei._norm_key("Pollo Campero"), "pollo_campero")
        self.assertEqual(ei._slug("Pollo Campero"), "pollo-campero")


class TestFreshnessCadence(unittest.TestCase):
    def test_founding_year_never_stale(self):
        self.assertFalse(ei.fact_is_stale("founding_year", "2000-01-01", today=date(2026, 8, 27)))

    def test_recent_ceo_fact_not_stale(self):
        self.assertFalse(ei.fact_is_stale("ceo_or_leadership", "2026-08-01", today=date(2026, 8, 27)))

    def test_old_ceo_fact_is_stale(self):
        self.assertTrue(ei.fact_is_stale("ceo_or_leadership", "2026-01-01", today=date(2026, 8, 27)))

    def test_missing_as_of_is_stale(self):
        self.assertTrue(ei.fact_is_stale("ceo_or_leadership", None))

    def test_unknown_fact_type_uses_default_cadence(self):
        # 90-day default: 100 days old should be stale, 10 days should not.
        self.assertTrue(ei.fact_is_stale("some_never_before_seen_fact_type", "2026-05-01", today=date(2026, 8, 27)))
        self.assertFalse(ei.fact_is_stale("some_never_before_seen_fact_type", "2026-08-17", today=date(2026, 8, 27)))


class TestCanonicalTechnologyRendering(unittest.TestCase):
    def test_semantic_layer_names_do_not_duplicate_graph_rows(self):
        account = {"technology_stack": [
            {"layer": "POS / unified commerce", "vendor": "Qu", "status": "announced", "confidence": "medium", "as_of": "2026-09-04"},
            {"layer": "voice AI", "vendor": "Presto", "status": "historical / discontinued", "confidence": "high", "as_of": "2023-11-01"},
        ]}
        graph = [
            {"status": "active", "category": "pos", "_other_entity_name": "Qu", "deployment_status": "confirmed", "confidence": {"level": "medium"}},
            {"status": "active", "category": "voice_ai", "_other_entity_name": "Presto", "deployment_status": "confirmed", "confidence": {"level": "medium"}},
        ]
        markdown = "\n".join(abb.render_technology_environment_section(account, graph))
        self.assertEqual(markdown.count("| Qu |"), 1)
        self.assertEqual(markdown.count("| Presto |"), 1)
        self.assertIn("historical / discontinued", markdown)


class TestHistoricalPreservationOnChange(unittest.TestCase):
    """impact_review._set_field_object must append the prior value to
    history[] rather than silently discard it -- the spec's 'Aloha retained
    as historical' requirement. Pure in-memory, no filesystem."""

    def test_value_change_preserves_prior_state_in_history(self):
        root = {"technology_stack": [{
            "layer": "POS", "value": "Aloha", "status": "Confirmed", "confidence": "high",
            "evidence_ids": ["ev-0001"], "as_of": "2025-01-01", "scope": "account",
            "last_reviewed_by": "human:todd",
        }]}
        change = ir.ProposedChange(
            json_path="technology_stack[0]", target_file="account.json",
            new_value="NewPOS", new_status="Confirmed", new_confidence="high",
            evidence_id="ev-0002", as_of="2026-08-27", reason="POS migration confirmed",
        )
        ir._set_field_object(root, change.json_path, change)
        row = root["technology_stack"][0]
        self.assertEqual(row["value"], "NewPOS")
        self.assertEqual(row["as_of"], "2026-08-27")
        self.assertIn("ev-0001", row["evidence_ids"])
        self.assertIn("ev-0002", row["evidence_ids"])
        self.assertEqual(len(row["history"]), 1)
        self.assertEqual(row["history"][0]["value"], "Aloha")
        self.assertEqual(row["history"][0]["as_of"], "2025-01-01")
        self.assertEqual(row["history"][0]["superseded_at"], "2026-08-27")

    def test_no_change_does_not_add_history_entry(self):
        root = {"technology_stack": [{
            "layer": "POS", "value": "Aloha", "status": "Confirmed", "confidence": "high",
            "evidence_ids": ["ev-0001"], "as_of": "2025-01-01", "scope": "account",
            "last_reviewed_by": "human:todd",
        }]}
        change = ir.ProposedChange(
            json_path="technology_stack[0]", target_file="account.json",
            new_value="Aloha", new_status="Confirmed", new_confidence="high",
            evidence_id="ev-0002", as_of="2026-08-27", reason="reverification, same value",
        )
        ir._set_field_object(root, change.json_path, change)
        self.assertNotIn("history", root["technology_stack"][0])

    def test_scalar_leaf_unaffected(self):
        root = {"buying_influences": [{"title": "COO"}]}
        change = ir.ProposedChange(
            json_path="buying_influences[0].title", target_file="account.json",
            new_value="CEO", new_status=None, new_confidence=None,
            evidence_id="ev-0003", as_of="2026-08-27", reason="title change",
        )
        ir._set_field_object(root, change.json_path, change)
        self.assertEqual(root["buying_influences"][0]["title"], "CEO")


class TestOrchestratorWithIsolatedFixtureAccount(unittest.TestCase):
    """Real filesystem I/O, but against a disposable account directory
    created and destroyed by this test class -- never the shared portfolio
    registry, never a real account slug."""

    def setUp(self):
        # Per-test (not per-class) fixture: generate_brief() mutates version
        # state on every call, so tests sharing one class-level fixture
        # would see each other's versions and produce flaky, order-dependent
        # results -- confirmed live while writing this suite.
        self.account_dir_path = cpc.ROOT / "accounts" / TEST_SLUG
        if self.account_dir_path.exists():
            shutil.rmtree(self.account_dir_path)
        self.account_dir_path.mkdir(parents=True)
        cpc.save_json(self.account_dir_path / "account.json", {
            "account_id": f"acct-{TEST_SLUG}", "account_slug": TEST_SLUG,
            "display_name": "Test Fixture Account", "aliases": [],
            "portfolio_status": {"value": "research", "status": "confirmed", "evidence_ids": [], "confidence": "high", "as_of": "2026-08-27", "scope": "account", "last_reviewed_by": "human:test"},
            "owners": [], "executive_summary": "Test summary.", "current_business_situation": "Test situation.",
            "leadership": {"confirmed": [], "reported_unverified": []},
            "technology_stack": [{
                "layer": "POS", "vendor": "OldVendor",
                "current_state": "seed", "status": "Verify", "confidence": "medium",
                "evidence_ids": [], "as_of": "2020-01-01", "scope": "account", "last_reviewed_by": "human:test",
            }],
            "commercial_models": [], "buying_influences": [], "opportunities": [],
            "qualification": {"criteria": []}, "strategic_position": {},
            "bottom_line": "Test bottom line.",
            "latest_review": {}, "template_version": "test", "updated_at": "2026-08-27T00:00:00Z",
        })
        cpc.save_json(self.account_dir_path / "brand_profile.json", {"account_id": f"acct-{TEST_SLUG}"})
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{TEST_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{TEST_SLUG}", "sources": []})
        (self.account_dir_path / "evidence.jsonl").write_text("", encoding="utf-8")

    def tearDown(self):
        if self.account_dir_path.exists():
            shutil.rmtree(self.account_dir_path)

    def test_resolve_account_finds_the_fixture_by_exact_slug(self):
        slug, exists = abb.resolve_account(TEST_SLUG)
        self.assertEqual(slug, TEST_SLUG)
        self.assertTrue(exists)

    def test_new_account_name_reports_not_existing(self):
        slug, exists = abb.resolve_account("Some Account That Has Never Been Seen Before XYZ")
        self.assertFalse(exists)

    def test_canonical_internal_structure_has_no_system_wrapper(self):
        markdown = abb.render_background_brief(TEST_SLUG, prepared_for="Todd Vahlsing")
        self.assertIn("## Executive Summary", markdown)
        self.assertIn("## Current Business Situation", markdown)
        self.assertIn("## Technology Environment", markdown)
        self.assertIn("## Bottom Line", markdown)
        self.assertNotIn("Prior Account Intelligence", markdown)
        self.assertNotIn("Additional documents available on request", markdown)
        self.assertIn("Prepared by: Todd Vahlsing", markdown)
        self.assertNotIn("Prepared for: Todd Vahlsing", markdown)
        self.assertNotIn("persisted RBB", markdown)
        self.assertNotIn("Generated ", markdown)

    def test_empty_leadership_headings_are_suppressed(self):
        markdown = abb.render_background_brief(TEST_SLUG)
        self.assertNotIn("## Leadership", markdown)
        self.assertNotIn("### Confirmed", markdown)
        self.assertNotIn("### Reported / Requires Validation", markdown)

    def test_internal_reference_is_listed_without_exposing_durable_system_id(self):
        cpc.save_json(self.account_dir_path / "source_index.json", {
            "account_id": f"acct-{TEST_SLUG}",
            "sources": [{
                "source_id": "ev-test-001",
                "type": "internal_reference",
                "description": "Internal account call",
                "durable_source_id": "capture:cap-secret",
                "event_date": "2026-09-08",
            }],
        })
        markdown = abb.render_background_brief(TEST_SLUG)
        self.assertIn("- Internal account call, 2026-09-08", markdown)
        self.assertNotIn("capture:cap-secret", markdown)

    def test_opportunity_hypotheses_persist_and_reload(self):
        abb.add_hypothesis(
            TEST_SLUG, hypothesis_id="hyp-test-0001", name="Test Hypothesis",
            description="A test hypothesis.", supporting_evidence_ids=[],
            validation_status="unvalidated",
        )
        hyps = abb.load_hypotheses(TEST_SLUG)
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0]["name"], "Test Hypothesis")
        # never auto-promoted into account.json's opportunities[]
        account = cpc.load_json(self.account_dir_path / "account.json")
        self.assertEqual(account["opportunities"], [])

    def test_discovery_questions_persist_and_reload(self):
        abb.add_discovery_question(
            TEST_SLUG, question_id="dq-test-0001", topic="Test Topic",
            question="Is this still true?", gap_type="stale",
        )
        qs = abb.load_discovery_questions(TEST_SLUG)
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["topic"], "Test Topic")

    def test_generate_brief_renders_real_persisted_data(self):
        result = abb.generate_brief(TEST_SLUG, generated_for="Unit Test")
        self.assertIn("Test Fixture Account", result["markdown"])
        self.assertIn("Test summary.", result["markdown"])
        self.assertEqual(result["version"]["version"], 1)

    def test_regenerating_creates_new_version_and_preserves_old(self):
        v1 = abb.generate_brief(TEST_SLUG, generated_for="Unit Test")
        history_dir = self.account_dir_path / "briefs" / "history"
        files_after_v1 = list(history_dir.glob("*.md")) if history_dir.exists() else []

        v2 = abb.generate_brief(TEST_SLUG, generated_for="Unit Test Round 2")
        self.assertEqual(v2["version"]["version"], v1["version"]["version"] + 1)

        files_after_v2 = list(history_dir.glob("*.md"))
        self.assertGreater(len(files_after_v2), len(files_after_v1), "prior current brief must be archived to history/, not discarded")

        registry = cpc.load_json(self.account_dir_path / "briefs" / "registry.json")
        versions = registry["versions"]
        superseded = [v for v in versions if v["superseded"]]
        current = [v for v in versions if not v["superseded"]]
        self.assertGreaterEqual(len(superseded), 1, "at least the first version must now be marked superseded")
        self.assertEqual(len(current), 1, "exactly one version must be current")

    def test_retrieve_existing_intelligence_does_not_reresearch(self):
        """Regression test for spec section 20/21: asking again must
        retrieve the persisted intelligence, not silently start over."""
        abb.generate_brief(TEST_SLUG, generated_for="Unit Test")
        intel = abb.retrieve_existing_intelligence(TEST_SLUG)
        self.assertIsNotNone(intel["prior_brief_version"])
        self.assertEqual(intel["dossier"]["account"]["display_name"], "Test Fixture Account")

    def test_assess_freshness_flags_old_tech_stack_row(self):
        intel = abb.retrieve_existing_intelligence(TEST_SLUG)
        freshness = abb.assess_freshness(intel)
        stale_paths = [f["path"] for f in freshness["stale"]]
        self.assertTrue(any("technology_stack" in p for p in stale_paths), f"expected a stale technology_stack entry, got: {freshness}")


class TestDeslugCandidatesApostropheRestoration(unittest.TestCase):
    """RB-DEFECT-2026-08-29: a real "churchs-chicken Background Brief" was
    generated with an empty Technology Environment despite RB's own graph
    already having real, active relationships for "Church's Texas Chicken"
    -- de-slugifying "churchs-chicken" produced "Churchs Chicken", which
    doesn't token-match a name with a real apostrophe (_norm_key treats an
    apostrophe as a word separator: "Church's" -> tokens {church, s}, but
    "Churchs" -> the single token {churchs}, never a subset of the former).
    Pure function, no filesystem."""

    def test_apostrophe_inserted_before_trailing_s(self):
        candidates = abb._deslug_candidates("churchs-chicken")
        self.assertIn("Church's Chicken", candidates)

    def test_original_and_plain_titlecase_still_included(self):
        candidates = abb._deslug_candidates("churchs-chicken")
        self.assertIn("churchs-chicken", candidates)
        self.assertIn("Churchs Chicken", candidates)

    def test_multi_word_name_only_apostrophizes_the_trailing_s_word(self):
        # str.title() doesn't know about McDonald's mid-word capital D --
        # that's fine, _resolve_brand_entity_id's matching is case-
        # insensitive, so the candidate still resolves correctly in
        # practice; this test only checks the apostrophe placement itself.
        candidates = abb._deslug_candidates("mcdonalds-restaurant-group")
        self.assertIn("Mcdonald's Restaurant Group", candidates)

    def test_short_word_not_apostrophized(self):
        """A 1-2 letter word ending in 's' (e.g. a real "Us" or "As") isn't
        a plausible possessive-brand-name candidate -- don't generate a
        nonsense "U's" from noise."""
        candidates = abb._deslug_candidates("us-foods")
        self.assertNotIn("U's Foods", candidates)


class TestTechnologyEnvironmentRendersEcosystemRelationships(unittest.TestCase):
    """RB-DEFECT-2026-08-29: retrieve_existing_intelligence() already
    computed ecosystem_relationships correctly, but render_technology_
    environment_section only ever read account.technology_stack (a
    separate, persisted field nothing populates from the graph) -- a real
    brand with real, active ecosystem_intelligence.json relationships and
    zero manually-curated technology_stack rows rendered a genuinely empty
    table. Pure function, no filesystem."""

    def _relationship(self, category, vendor_name, *, status="active", deployment_status="confirmed", level="high"):
        return {
            "status": status, "category": category, "deployment_status": deployment_status,
            "confidence": {"level": level}, "updated_at": "2026-08-29T12:00:00Z",
            "_other_entity_name": vendor_name,
        }

    def test_ecosystem_relationship_rendered_when_no_persisted_row(self):
        lines = abb.render_technology_environment_section(
            {"technology_stack": []},
            [self._relationship("payments", "Worldpay")],
        )
        text = "\n".join(lines)
        self.assertIn("Worldpay", text)
        self.assertIn("payments", text)

    def test_persisted_row_wins_over_ecosystem_relationship_for_same_layer(self):
        """A human's own curated technology_stack entry must not be
        silently displaced by a graph-derived one for the same layer."""
        lines = abb.render_technology_environment_section(
            {"technology_stack": [{"layer": "payments", "vendor": "HumanVerifiedVendor", "status": "confirmed", "confidence": "high", "as_of": "2026-01-01"}]},
            [self._relationship("payments", "Worldpay")],
        )
        text = "\n".join(lines)
        self.assertIn("HumanVerifiedVendor", text)
        self.assertNotIn("Worldpay", text)

    def test_inactive_relationship_not_rendered(self):
        lines = abb.render_technology_environment_section(
            {"technology_stack": []},
            [self._relationship("pos", "OldVendor", status="historical")],
        )
        self.assertNotIn("OldVendor", "\n".join(lines))

    def test_multiple_categories_all_render(self):
        lines = abb.render_technology_environment_section(
            {"technology_stack": []},
            [
                self._relationship("loyalty", "PAR Technology"),
                self._relationship("pos", "Qu"),
                self._relationship("payments", "Worldpay"),
            ],
        )
        text = "\n".join(lines)
        for vendor in ("PAR Technology", "Qu", "Worldpay"):
            self.assertIn(vendor, text)

    def test_no_ecosystem_relationships_and_no_tech_stack_renders_empty_table(self):
        lines = abb.render_technology_environment_section({"technology_stack": []}, [])
        text = "\n".join(lines)
        self.assertNotIn("| Layer |", text)


class TestCreateNewAccountResolvesRealEntityName(unittest.TestCase):
    """RB-DEFECT-2026-08-29: create_new_account(name) used to store
    whatever string the caller passed (often account_slug, e.g.
    "churchs-chicken") verbatim as display_name -- both a cosmetic defect
    (brief title "churchs-chicken Background Brief") and a substantive one
    (generate_brief's own entity resolution then failed to match the real
    "Church's Texas Chicken" graph entity). Mocks account_research_common's
    registry/shell writers so this never touches the real shared registry
    (test file's own stated constraint)."""

    def setUp(self):
        self._patchers = [
            unittest.mock.patch.object(abb.cpc, "create_pre_engagement_shell"),
            unittest.mock.patch.object(abb.cpc, "load_registry", return_value={"registry": []}),
            unittest.mock.patch.object(abb.cpc, "save_registry"),
            unittest.mock.patch.object(abb.intelligence_index, "register_document"),
            # RB-2026-08-29: create_new_account now also seeds starter
            # discovery questions, which writes a real file via
            # cpc.account_dir() -- create_pre_engagement_shell being mocked (a
            # no-op) does NOT prevent that write, since account_dir() never
            # checks the shell actually ran. Confirmed live: an earlier
            # version of this test really did write discovery_questions.json
            # into the shared system/account_research/accounts/ tree.
            # Mocked here (this test's own purpose is name resolution, not
            # question seeding -- that has its own dedicated test below).
            unittest.mock.patch.object(abb, "seed_starter_discovery_questions"),
        ]
        self.mocks = {p.attribute: p.start() for p in self._patchers}

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_slug_shaped_input_resolves_to_the_real_graph_entity_name(self):
        graph = ei._read_graph()
        entity_id, is_new = ei._resolve_brand_entity_id("Church's Chicken", graph)
        if is_new or not entity_id:
            self.skipTest("no 'Church's Texas Chicken'-matching entity in the live graph to test against")
        slug = abb.create_new_account("churchs-chicken")
        self.assertNotEqual(slug, "churchs-chicken")
        called_display_name = abb.cpc.create_pre_engagement_shell.call_args.kwargs.get("display_name")
        self.assertNotEqual(called_display_name, "churchs-chicken")

    def test_new_account_seeds_starter_discovery_questions(self):
        abb.create_new_account("Some Brand Name")
        abb.seed_starter_discovery_questions.assert_called_once()


class TestSeedStarterDiscoveryQuestions(unittest.TestCase):
    """RB-DEFECT-2026-08-29: Todd, on a real generated brief for a brand-new
    account: "This is not the canonical standard for the background
    brief." The canonical reference (Cafe Rio) has 29 real discovery
    questions across 5 categories; the render function already correctly
    renders them, but nothing ever generated a starter set for a genuinely
    new account, so the whole "## Key Discovery Questions" section was
    silently absent -- even though RB's own KB instructions already
    promise "real Discovery Questions" for a brand RBB knows little about.
    Real disposable fixture account, not the shared registry."""

    SEED_TEST_SLUG = "test-fixture-seed-discovery-questions"

    def setUp(self):
        self.account_dir_path = cpc.ROOT / "accounts" / self.SEED_TEST_SLUG
        if self.account_dir_path.exists():
            shutil.rmtree(self.account_dir_path)
        self.account_dir_path.mkdir(parents=True)
        cpc.save_json(self.account_dir_path / "account.json", {
            "account_id": f"acct-{self.SEED_TEST_SLUG}", "account_slug": self.SEED_TEST_SLUG,
            "display_name": "Test Seed Brand", "aliases": [],
        })

    def tearDown(self):
        if self.account_dir_path.exists():
            shutil.rmtree(self.account_dir_path)

    def test_seeds_the_full_starter_set(self):
        count = abb.seed_starter_discovery_questions(self.SEED_TEST_SLUG)
        self.assertEqual(count, len(abb._STARTER_DISCOVERY_QUESTIONS))
        questions = abb.load_discovery_questions(self.SEED_TEST_SLUG)
        self.assertEqual(len(questions), len(abb._STARTER_DISCOVERY_QUESTIONS))

    def test_seeded_questions_render_in_the_brief(self):
        abb.seed_starter_discovery_questions(self.SEED_TEST_SLUG)
        cpc.save_json(self.account_dir_path / "brand_profile.json", {"account_id": f"acct-{self.SEED_TEST_SLUG}"})
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{self.SEED_TEST_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{self.SEED_TEST_SLUG}", "sources": []})
        (self.account_dir_path / "evidence.jsonl").write_text("", encoding="utf-8")
        markdown = abb.render_background_brief(self.SEED_TEST_SLUG)
        self.assertIn("## Key Discovery Questions", markdown)
        self.assertIn("What POS system is currently deployed?", markdown)

    def test_idempotent_no_duplicates_on_rerun(self):
        abb.seed_starter_discovery_questions(self.SEED_TEST_SLUG)
        abb.seed_starter_discovery_questions(self.SEED_TEST_SLUG)
        questions = abb.load_discovery_questions(self.SEED_TEST_SLUG)
        self.assertEqual(len(questions), len(abb._STARTER_DISCOVERY_QUESTIONS))

    def test_covers_all_five_reference_categories(self):
        abb.seed_starter_discovery_questions(self.SEED_TEST_SLUG)
        questions = abb.load_discovery_questions(self.SEED_TEST_SLUG)
        topics = {q["topic"] for q in questions}
        self.assertEqual(topics, {
            "Business and Operating Priorities", "Loyalty and Digital",
            "Payments", "Restaurant Technology", "Decision Process",
        })


if __name__ == "__main__":
    unittest.main()
