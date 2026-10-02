"""
test_competitor_intelligence.py — RB-2026-08-28.

Real gap: RB persists intelligence on restaurant BRANDS (macro graph,
account research, Blue Sheets) but had no equivalent durable artifact for
the vendors competing with Genius (PAR, Toast, Oracle, NCR, Qu, Nory,
Restaurant365, Revel), despite real competitive-analysis work already
happening. Fed two ways per Todd's direction: mechanically from
ecosystem_intelligence.json signals + account_intelligence/ docs (the
intelligence-gathering process), and from structured user-supplied notes.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import competitor_intelligence as ci  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = cic.ROOT
        cic.ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        cic.ROOT = self._orig_root
        self._tmpdir.cleanup()


class TestCompetitorShellAndStorage(_IsolatedRootMixin, unittest.TestCase):
    def test_create_and_load_shell(self):
        d = cic.create_competitor_shell("acme-pos", "Acme POS", "vendor-acme-pos")
        self.assertTrue((d / "competitor.json").exists())
        self.assertTrue((d / "evidence.jsonl").exists())
        data = cic.load_competitor("acme-pos")
        self.assertEqual(data["competitor"]["display_name"], "Acme POS")
        self.assertEqual(data["competitor"]["vendor_entity_id"], "vendor-acme-pos")
        self.assertEqual(data["evidence"], [])

    def test_missing_competitor_raises(self):
        with self.assertRaises(FileNotFoundError):
            cic.load_competitor("does-not-exist")

    def test_registry_records_new_competitor(self):
        cic.create_competitor_shell("acme-pos", "Acme POS", None)
        cic.register_competitor("acme-pos", "Acme POS")
        reg = cic.load_registry()
        slugs = [e["competitor_slug"] for e in reg["registry"]]
        self.assertIn("acme-pos", slugs)


class TestAddCompetitiveNote(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", None)

    def test_add_note_appends_evidence(self):
        result = ci.add_competitive_note("acme-pos", "Acme raised prices 20% in Q2", category="pricing")
        self.assertTrue(result["ok"])
        data = cic.load_competitor("acme-pos")
        self.assertEqual(len(data["evidence"]), 1)
        self.assertEqual(data["evidence"][0]["category"], "pricing")
        self.assertEqual(data["evidence"][0]["summary"], "Acme raised prices 20% in Q2")

    def test_pov_category_sets_todds_pov_field(self):
        ci.add_competitive_note("acme-pos", "Acme is overextended technically", category="pov")
        data = cic.load_competitor("acme-pos")
        self.assertEqual(data["competitor"]["todds_pov"], "Acme is overextended technically")

    def test_invalid_category_rejected(self):
        with self.assertRaises(ValueError):
            ci.add_competitive_note("acme-pos", "note", category="not_a_real_category")

    def test_empty_note_rejected(self):
        with self.assertRaises(ValueError):
            ci.add_competitive_note("acme-pos", "   ", category="other")

    def test_notes_never_invented_only_structured_input_accepted(self):
        """The function only ever stores exactly what's passed in -- no
        interpretation layer that could silently alter or embellish a
        human-supplied fact."""
        text = "Exact verbatim customer quote about Acme's support quality"
        ci.add_competitive_note("acme-pos", text, category="weakness", source="Frank Fennell")
        data = cic.load_competitor("acme-pos")
        self.assertEqual(data["evidence"][-1]["summary"], text)
        self.assertEqual(data["evidence"][-1]["source"], "Frank Fennell")


class TestAddExtendedProfileFinding(_IsolatedRootMixin, unittest.TestCase):
    """2026-09-28: closes the gap Team Portal's Competitor Snapshot card
    was showing honest-empty for every vendor -- products/strengths/
    weaknesses/vulnerabilities/key_customers/recent_news/trends had a
    storage shape (extended_field()) but no writer."""

    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", None)

    def test_appends_to_list_field(self):
        result = ci.add_extended_profile_finding(
            "acme-pos", "strengths", "Strong franchisee NPS", confidence="high",
            source_url="https://example.com/report",
        )
        self.assertTrue(result["ok"])
        data = cic.load_competitor("acme-pos")["competitor"]
        self.assertEqual(len(data["strengths"]), 1)
        self.assertEqual(data["strengths"][0]["value"], "Strong franchisee NPS")
        self.assertEqual(data["strengths"][0]["source_url"], "https://example.com/report")

    def test_trends_is_a_single_field_not_a_list(self):
        ci.add_extended_profile_finding("acme-pos", "trends", "Shifting to cloud-native architecture")
        data = cic.load_competitor("acme-pos")["competitor"]
        self.assertEqual(data["trends"]["value"], "Shifting to cloud-native architecture")

    def test_dedupes_identical_value_in_list_field(self):
        ci.add_extended_profile_finding("acme-pos", "products", "Acme Kiosk")
        result = ci.add_extended_profile_finding("acme-pos", "products", "Acme Kiosk")
        self.assertTrue(result["deduped"])
        data = cic.load_competitor("acme-pos")["competitor"]
        self.assertEqual(len(data["products"]), 1)

    def test_invalid_field_rejected(self):
        with self.assertRaises(ValueError):
            ci.add_extended_profile_finding("acme-pos", "not_a_real_field", "value")

    def test_empty_value_rejected(self):
        with self.assertRaises(ValueError):
            ci.add_extended_profile_finding("acme-pos", "strengths", "   ")


class TestGapAnalysis(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", None)

    def test_set_competes_on_valid(self):
        result = ci.set_competes_on("acme-pos", ["pos", "payments"])
        self.assertEqual(result["competes_on"], ["payments", "pos"])

    def test_set_competes_on_rejects_unknown_product_line(self):
        with self.assertRaises(ValueError):
            ci.set_competes_on("acme-pos", ["pos", "not_a_real_product_line"])

    def test_add_gap_point_genius_side(self):
        ci.add_gap_point("acme-pos", "genius", "Genius has deeper back-office integration")
        data = cic.load_competitor("acme-pos")
        self.assertEqual(len(data["competitor"]["vs_genius"]["genius_advantages"]), 1)
        self.assertEqual(len(data["competitor"]["vs_genius"]["competitor_advantages"]), 0)

    def test_add_gap_point_competitor_side(self):
        ci.add_gap_point("acme-pos", "competitor", "Acme has a cleaner UI")
        data = cic.load_competitor("acme-pos")
        self.assertEqual(len(data["competitor"]["vs_genius"]["competitor_advantages"]), 1)

    def test_add_gap_point_invalid_side_rejected(self):
        with self.assertRaises(ValueError):
            ci.add_gap_point("acme-pos", "nobody", "some point")

    def test_add_gap_point_with_category(self):
        """2026-09-28: lets a gap point be tagged as specific to one
        Genius product line (e.g. a POS-only weakness), so Value Wedge's
        Circle 3 can filter out off-topic points for a category-scoped
        wedge -- an untagged point stays company-wide/always-applicable."""
        ci.add_gap_point("acme-pos", "genius", "Genius has deeper POS integration", category="pos")
        data = cic.load_competitor("acme-pos")
        self.assertEqual(data["competitor"]["vs_genius"]["genius_advantages"][0]["category"], "pos")

    def test_add_gap_point_no_category_omits_key(self):
        ci.add_gap_point("acme-pos", "genius", "Company-wide financial weakness")
        data = cic.load_competitor("acme-pos")
        self.assertNotIn("category", data["competitor"]["vs_genius"]["genius_advantages"][0])

    def test_add_gap_point_invalid_category_rejected(self):
        with self.assertRaises(ValueError):
            ci.add_gap_point("acme-pos", "genius", "some point", category="not_a_real_product_line")


class TestCorrectGapPoint(_IsolatedRootMixin, unittest.TestCase):
    """Real 2026-10-02 finding: add_gap_point() is append-only by design,
    with no path for fixing a point that was simply wrong (as opposed to
    adding a new one). PAR Technology's profile had a factually incorrect
    "Genius wins: PAR does not process payments itself" point -- PAR does
    have its own payments/gateway (on Vantiv rails), it's bolted on rather
    than payments-first. correct_gap_point() is the real fix path."""

    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", None)
        ci.add_gap_point("acme-pos", "genius", "Genius wins on integration depth")
        ci.add_gap_point("acme-pos", "genius", "Genius wins on integration depth")  # duplicate, for ambiguity test
        ci.add_gap_point("acme-pos", "competitor", "Acme wins on price")

    def test_corrects_matching_point_in_place(self):
        result = ci.correct_gap_point(
            "acme-pos", "competitor", "Acme wins on price", "Acme wins on price in SMB only",
            evidence_id="note-0099",
        )
        entries = result["vs_genius"]["competitor_advantages"]
        self.assertEqual(len(entries), 1)  # corrected in place, not appended
        self.assertEqual(entries[0]["point"], "Acme wins on price in SMB only")
        self.assertEqual(entries[0]["evidence_id"], "note-0099")
        self.assertIn("corrected_at", entries[0])

    def test_no_matching_point_raises(self):
        with self.assertRaises(ValueError):
            ci.correct_gap_point("acme-pos", "competitor", "a point that was never added", "new text")

    def test_ambiguous_match_raises(self):
        with self.assertRaises(ValueError):
            ci.correct_gap_point("acme-pos", "genius", "Genius wins on integration depth", "new text")

    def test_invalid_side_rejected(self):
        with self.assertRaises(ValueError):
            ci.correct_gap_point("acme-pos", "nobody", "Acme wins on price", "new text")

    def test_empty_new_point_rejected(self):
        with self.assertRaises(ValueError):
            ci.correct_gap_point("acme-pos", "competitor", "Acme wins on price", "   ")

    def test_correction_never_touches_the_other_side(self):
        ci.correct_gap_point("acme-pos", "competitor", "Acme wins on price", "corrected")
        data = cic.load_competitor("acme-pos")
        genius_points = [e["point"] for e in data["competitor"]["vs_genius"]["genius_advantages"]]
        self.assertEqual(genius_points, ["Genius wins on integration depth", "Genius wins on integration depth"])


class TestRenderCompetitorProfile(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", None)

    def test_render_includes_positioning_and_pov(self):
        data = cic.load_competitor("acme-pos")
        comp = data["competitor"]
        comp["positioning_summary"] = "Acme is the discount option in the market."
        comp["todds_pov"] = "Acme will not survive an enterprise RFP."
        cic.save_json(cic.competitor_dir("acme-pos") / "competitor.json", comp)
        md = ci.render_competitor_profile("acme-pos")
        self.assertIn("Acme is the discount option", md)
        self.assertIn("Acme will not survive", md)

    def test_render_includes_gap_analysis(self):
        ci.add_gap_point("acme-pos", "genius", "Genius wins on integration depth")
        ci.add_gap_point("acme-pos", "competitor", "Acme wins on price")
        md = ci.render_competitor_profile("acme-pos")
        self.assertIn("Genius wins:", md)
        self.assertIn("Acme POS wins:", md)
        self.assertIn("Genius wins on integration depth", md)
        self.assertIn("Acme wins on price", md)

    def test_render_groups_evidence_by_category(self):
        ci.add_competitive_note("acme-pos", "Lost the Wendy's RFP", category="customer_loss")
        ci.add_competitive_note("acme-pos", "Won the Sonic pilot", category="customer_win")
        md = ci.render_competitor_profile("acme-pos")
        self.assertIn("Customer wins", md)
        self.assertIn("Customer losses", md)
        self.assertIn("Won the Sonic pilot", md)
        self.assertIn("Lost the Wendy's RFP", md)

    def test_render_empty_profile_does_not_crash(self):
        md = ci.render_competitor_profile("acme-pos")
        self.assertIn("Acme POS", md)


class TestRenderCompetitorProfileCategoryBattleCards(_IsolatedRootMixin, unittest.TestCase):
    """RB-2026-09-25 rendered category_battle_cards content (RM posture,
    discovery questions, "when to bring Todd in", ...) inside Competitor
    Profile, alongside the category-scoped Battle Card (battle_card.py)
    that already rendered the exact same content. RB-2026-09-28 reverses
    this: live Team Portal use showed this content -- RM-specific
    language, Todd named by name -- reaching every teammate through
    Competitor Profile, which has no equivalent redaction for it (only
    the separate '## Todd's POV' section is redacted). Category battle
    card content now renders ONLY in the dedicated Battle Card artifact;
    Competitor Profile never shows it, for anyone, regardless of what's
    on file."""

    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", None)

    def test_no_category_battle_cards_renders_no_section(self):
        md = ci.render_competitor_profile("acme-pos")
        self.assertNotIn("Category Battle Cards", md)

    def test_populated_category_battle_card_still_does_not_render(self):
        ci.upsert_category_battle_card(
            "acme-pos", "pos", status="todd_validated", confidence_pct=80,
            rm_plain_english_posture="They win on price, we win on integration depth.",
        )
        ci.add_category_battle_card_point("acme-pos", "pos", "discovery_questions", "What's your contract renewal date?")
        ci.add_category_battle_card_point("acme-pos", "pos", "red_flags", "They'll offer a steep discount to renew.")
        md = ci.render_competitor_profile("acme-pos")
        self.assertNotIn("Category Battle Cards", md)
        self.assertNotIn("RM posture", md)
        self.assertNotIn("They win on price, we win on integration depth.", md)
        self.assertNotIn("What's your contract renewal date?", md)
        self.assertNotIn("They'll offer a steep discount to renew.", md)


class TestRenderCompetitorProfileAutoLinkedReferences(_IsolatedRootMixin, unittest.TestCase):
    """RB-2026-09-28: account_reference_detector.link_references() appends
    a fact-free 'this uploaded document mentions this competitor' nudge for
    Todd to review -- its own docstring says it should never be treated as
    a citable fact. render_competitor_profile() rendered its raw `summary`
    (up to 400 chars straight from whatever document matched) anyway,
    caught live: Todd's own confidential Pollo Campero RFP/SOW pricing
    documents surfaced verbatim on PAR Technology's Competitor Profile
    (source: 'PAR' matching somewhere in those documents), visible to every
    Team Portal viewer including teammates. Only the pointer should render,
    never the excerpt."""

    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", None)

    def test_auto_linked_reference_shows_pointer_not_excerpt(self):
        record = {
            "evidence_id": "ref-0001",
            "logged_at": cic.now_iso(),
            "category": "other",
            "summary": "Vendor Name | Global Payments / Genius | Primary Contact | Todd Vahlsing | Pricing Validity | 90 days",
            "source": "SOW - MenuBoards (1).xlsx",
            "confidence": None,
            "document_id": "doc-abc123",
            "_source_title": "SOW - MenuBoards (1).xlsx",
        }
        cic.append_jsonl(cic.competitor_dir("acme-pos") / "evidence.jsonl", record)
        md = ci.render_competitor_profile("acme-pos")
        self.assertNotIn("Pricing Validity", md)
        self.assertNotIn("Todd Vahlsing", md)
        self.assertIn("An uploaded document mentions this competitor", md)
        self.assertIn("SOW - MenuBoards (1).xlsx", md)

    def test_manually_supplied_evidence_still_shows_its_summary(self):
        """Confirms the fix is scoped to _source_title records only --
        ordinary evidence (add_competitive_note, sync_from_ecosystem)
        still renders its real summary text."""
        record = {
            "evidence_id": "note-0001", "logged_at": cic.now_iso(), "category": "other",
            "summary": "A real, vetted competitive note about Acme POS.",
            "source": "Todd Vahlsing", "confidence": "high",
        }
        cic.append_jsonl(cic.competitor_dir("acme-pos") / "evidence.jsonl", record)
        md = ci.render_competitor_profile("acme-pos")
        self.assertIn("A real, vetted competitive note about Acme POS.", md)


class TestCategoryBattleCards(_IsolatedRootMixin, unittest.TestCase):
    """RB-2026-09-01 — category-scoped RM battle cards, additive on top of
    the vendor-level fields above."""

    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", None)

    def test_upsert_creates_and_sets_fields(self):
        result = ci.upsert_category_battle_card(
            "acme-pos", "pos", status="draft", confidence_pct=40,
            rm_plain_english_posture="Ask what problem the current vendor solves.",
        )
        self.assertTrue(result["ok"])
        card = result["battle_card"]
        self.assertEqual(card["status"], "draft")
        self.assertEqual(card["confidence_pct"], 40)
        self.assertEqual(card["rm_plain_english_posture"], "Ask what problem the current vendor solves.")
        self.assertIsNotNone(card["last_validated"])  # bumped because status was passed

    def test_upsert_partial_update_leaves_other_fields_untouched(self):
        ci.upsert_category_battle_card("acme-pos", "pos", status="draft", confidence_pct=40)
        ci.upsert_category_battle_card("acme-pos", "pos", confidence_pct=75)
        data = cic.load_competitor("acme-pos")
        card = data["competitor"]["category_battle_cards"]["pos"]
        self.assertEqual(card["status"], "draft")  # unchanged
        self.assertEqual(card["confidence_pct"], 75)  # updated

    def test_upsert_rejects_invalid_category(self):
        with self.assertRaises(ValueError):
            ci.upsert_category_battle_card("acme-pos", "not_a_real_category", status="draft")

    def test_upsert_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            ci.upsert_category_battle_card("acme-pos", "pos", status="not_a_real_status")

    def test_upsert_rejects_out_of_range_confidence(self):
        with self.assertRaises(ValueError):
            ci.upsert_category_battle_card("acme-pos", "pos", confidence_pct=150)
        with self.assertRaises(ValueError):
            ci.upsert_category_battle_card("acme-pos", "pos", confidence_pct=-1)

    def test_platform_and_delivery_aggregation_are_valid_categories(self):
        # RM-only categories with no per-brand market-share data source yet.
        ci.upsert_category_battle_card("acme-pos", "platform", status="draft")
        ci.upsert_category_battle_card("acme-pos", "delivery_aggregation", status="draft")
        data = cic.load_competitor("acme-pos")
        self.assertIn("platform", data["competitor"]["category_battle_cards"])
        self.assertIn("delivery_aggregation", data["competitor"]["category_battle_cards"])

    def test_add_point_appends_to_correct_field(self):
        ci.upsert_category_battle_card("acme-pos", "pos", status="draft")
        ci.add_category_battle_card_point("acme-pos", "pos", "discovery_questions", "What contract renewal date?")
        data = cic.load_competitor("acme-pos")
        card = data["competitor"]["category_battle_cards"]["pos"]
        self.assertEqual(card["discovery_questions"], ["What contract renewal date?"])
        self.assertEqual(card["listen_for"], [])

    def test_add_point_rejects_invalid_field(self):
        ci.upsert_category_battle_card("acme-pos", "pos", status="draft")
        with self.assertRaises(ValueError):
            ci.add_category_battle_card_point("acme-pos", "pos", "not_a_real_field", "x")

    def test_add_point_requires_card_to_exist_first(self):
        with self.assertRaises(ValueError):
            ci.add_category_battle_card_point("acme-pos", "pos", "listen_for", "x")

    def test_category_battle_card_never_touches_vendor_level_fields(self):
        """Regression guard: category_battle_cards is additive, never
        overwrites positioning_summary/todds_pov/vs_genius."""
        ci.add_competitive_note("acme-pos", "Acme is the discount option.", category="positioning")
        data = cic.load_competitor("acme-pos")
        data["competitor"]["positioning_summary"] = "Acme is the discount option in the market."
        cic.save_json(cic.competitor_dir("acme-pos") / "competitor.json", data["competitor"])
        ci.add_gap_point("acme-pos", "genius", "Genius wins on integration depth")

        ci.upsert_category_battle_card("acme-pos", "pos", status="draft", rm_plain_english_posture="talk track")
        ci.add_category_battle_card_point("acme-pos", "pos", "red_flags", "watch for this")

        data = cic.load_competitor("acme-pos")
        comp = data["competitor"]
        self.assertEqual(comp["positioning_summary"], "Acme is the discount option in the market.")
        self.assertEqual(len(comp["vs_genius"]["genius_advantages"]), 1)

    def test_pov_side_effect_still_fires_after_category_battle_card_write(self):
        """Regression guard: add_competitive_note(category='pov')'s
        todds_pov side effect must be unaffected by category_battle_cards."""
        ci.upsert_category_battle_card("acme-pos", "pos", status="draft")
        ci.add_competitive_note("acme-pos", "Acme is overextended technically", category="pov")
        data = cic.load_competitor("acme-pos")
        self.assertEqual(data["competitor"]["todds_pov"], "Acme is overextended technically")


class TestOwnCompanyExclusion(unittest.TestCase):
    """RB-DEFECT-071 (2026-09-11): the FSTEC bulk-import batch manually
    excluded Genius/Global Payments -- no code-level guard existed."""

    def test_matches_own_company_variants(self):
        for name in ("Genius", "Genius for Restaurants", "Global Payments",
                     "Global Payments Inc.", "GlobalPayments Corporation"):
            self.assertTrue(cic.is_own_company(name), name)

    def test_does_not_match_real_competitors(self):
        for name in ("Square", "PAR Technology", "Genius Sports"):
            self.assertFalse(cic.is_own_company(name), name)


class TestRegistryAtomicityAndSelfHealing(_IsolatedRootMixin, unittest.TestCase):
    """RB-DEFECT-071 (2026-09-11): the live incident -- 8 concurrent
    createCompetitor calls raced an unlocked, non-atomic registry
    read-modify-write, corrupting the file and permanently 500ing
    listCompetitors. These are the direct regression tests for that."""

    def test_concurrent_registrations_do_not_corrupt_registry(self):
        names = [f"Vendor {i}" for i in range(20)]
        threads = [
            threading.Thread(target=cic.register_competitor, args=(f"vendor-{i}", name))
            for i, name in enumerate(names)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # The file must still be valid JSON -- the exact failure mode that
        # took listCompetitors down (JSONDecodeError on every read).
        reg = json.loads(cic.registry_path().read_text(encoding="utf-8"))
        slugs = {e["competitor_slug"] for e in reg["registry"]}
        self.assertEqual(slugs, {f"vendor-{i}" for i in range(20)})

    def test_registry_file_written_atomically_no_stray_tmp_file(self):
        cic.register_competitor("acme-pos", "Acme POS")
        leftover = list(cic.registry_path().parent.glob("*.tmp"))
        self.assertEqual(leftover, [])

    def test_load_registry_self_heals_from_corrupted_file(self):
        cic.create_competitor_shell("acme-pos", "Acme POS", None)
        cic.create_competitor_shell("beta-payments", "Beta Payments", None)
        registry_path = cic.registry_path()
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        # Exact corruption shape from the real incident: a complete valid
        # document followed by a losing writer's leftover trailing bytes.
        registry_path.write_text(
            '{"registry": [{"competitor_slug": "acme-pos", "display_name": '
            '"Acme POS", "first_tracked_at": "2026-01-01T00:00:00Z", '
            '"last_updated_at": "2026-01-01T00:00:00Z"}]}\n}\n  ]\n}\n}\n',
            encoding="utf-8",
        )
        reg = cic.load_registry()
        slugs = {e["competitor_slug"] for e in reg["registry"]}
        # Both real directories present -- the corrupted file only knew
        # about acme-pos; beta-payments is recovered from disk.
        self.assertEqual(slugs, {"acme-pos", "beta-payments"})

    def test_load_registry_self_heal_preserves_recoverable_history(self):
        cic.create_competitor_shell("acme-pos", "Acme POS", None)
        registry_path = cic.registry_path()
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            '{"registry": [{"competitor_slug": "acme-pos", "display_name": '
            '"Acme POS", "first_tracked_at": "2026-01-01T00:00:00Z", '
            '"last_updated_at": "2026-01-01T00:00:00Z"}]}\ngarbage-tail',
            encoding="utf-8",
        )
        reg = cic.load_registry()
        entry = next(e for e in reg["registry"] if e["competitor_slug"] == "acme-pos")
        self.assertEqual(entry["first_tracked_at"], "2026-01-01T00:00:00Z")

    def test_load_registry_self_heal_persists_repair(self):
        cic.create_competitor_shell("acme-pos", "Acme POS", None)
        registry_path = cic.registry_path()
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("not json at all", encoding="utf-8")
        cic.load_registry()
        # The repair should have been written back -- a second read (a real
        # file parse, not the in-memory result) must not need to self-heal again.
        reraised = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertIn("acme-pos", {e["competitor_slug"] for e in reraised["registry"]})

    def test_load_registry_missing_file_returns_empty_not_an_error(self):
        self.assertEqual(cic.load_registry(), {"registry": []})


class TestEnsureCompetitorIdempotencyAndSelfHealing(_IsolatedRootMixin, unittest.TestCase):
    """RB-DEFECT-071 (2026-09-11): ensure_competitor() used to gate
    register_competitor() behind `if is_new:` -- a shell created but never
    registered (exactly what happened to 141 of the FSTEC batch) could
    never self-heal on retry, since a second attempt saw the shell already
    existed and treated it as "not new"."""

    def test_returns_slug_and_already_tracked_false_for_new_competitor(self):
        slug, already_tracked = ci.ensure_competitor("Acme POS")
        self.assertEqual(slug, "acme-pos")
        self.assertFalse(already_tracked)

    def test_second_call_reports_already_tracked_true(self):
        ci.ensure_competitor("Acme POS")
        slug, already_tracked = ci.ensure_competitor("Acme POS")
        self.assertEqual(slug, "acme-pos")
        self.assertTrue(already_tracked)

    def test_second_call_does_not_duplicate_registry_entry(self):
        ci.ensure_competitor("Acme POS")
        ci.ensure_competitor("Acme POS")
        reg = cic.load_registry()
        matches = [e for e in reg["registry"] if e["competitor_slug"] == "acme-pos"]
        self.assertEqual(len(matches), 1)

    def test_shell_created_but_never_registered_self_heals_on_retry(self):
        """The exact incident shape: create_competitor_shell() succeeded,
        register_competitor() never ran (simulating the registry write
        that failed under the race). A retry must register it now, not
        skip registration because the shell already exists."""
        cic.create_competitor_shell("acme-pos", "Acme POS", None)
        reg = cic.load_registry()
        self.assertEqual(reg["registry"], [])  # confirmed: not yet registered

        slug, already_tracked = ci.ensure_competitor("Acme POS")

        self.assertEqual(slug, "acme-pos")
        reg = cic.load_registry()
        self.assertIn("acme-pos", {e["competitor_slug"] for e in reg["registry"]})


class TestGenerateProfileStructuredErrors(_IsolatedRootMixin, unittest.TestCase):
    def test_generate_profile_returns_already_tracked_field(self):
        result = ci.generate_profile("Acme POS")
        self.assertIn("already_tracked", result)
        self.assertFalse(result["already_tracked"])
        second = ci.generate_profile("Acme POS")
        self.assertTrue(second["already_tracked"])

    def test_sync_stage_failure_raises_structured_error(self):
        with patch.object(ci, "sync_from_ecosystem", side_effect=RuntimeError("boom")):
            with self.assertRaises(ci.CompetitorCreationError) as cm:
                ci.generate_profile("Acme POS")
        exc = cm.exception
        self.assertEqual(exc.stage, "sync")
        self.assertTrue(exc.shell_created)
        self.assertTrue(exc.registered)
        # The shell/registration must have actually landed even though sync failed.
        self.assertIn("acme-pos", {e["competitor_slug"] for e in cic.load_registry()["registry"]})

    def test_register_stage_failure_raises_structured_error_with_shell_created_true(self):
        with patch.object(cic, "register_competitor", side_effect=RuntimeError("boom")):
            with self.assertRaises(ci.CompetitorCreationError) as cm:
                ci.generate_profile("Acme POS")
        exc = cm.exception
        self.assertEqual(exc.stage, "register")
        self.assertTrue(exc.shell_created)
        self.assertFalse(exc.registered)


class TestResolveCompetitorAgainstRealGraph(unittest.TestCase):
    """Uses the REAL ecosystem_intelligence.json (read-only) to confirm
    resolution against actual vendor entities works -- no writes here."""

    def test_resolves_known_vendor_by_alias(self):
        slug, vendor_entity_id, _is_new = ci.resolve_competitor("PAR")
        self.assertEqual(slug, "par-technology")
        self.assertEqual(vendor_entity_id, "vendor-par-technology")

    def test_resolves_known_vendor_by_canonical_name(self):
        slug, vendor_entity_id, _is_new = ci.resolve_competitor("Toast")
        self.assertEqual(slug, "toast")
        self.assertEqual(vendor_entity_id, "vendor-toast")

    def test_unknown_vendor_falls_back_to_slugified_name_no_entity(self):
        slug, vendor_entity_id, _is_new = ci.resolve_competitor("Some Brand New Vendor Xyz")
        self.assertEqual(slug, "some-brand-new-vendor-xyz")
        self.assertIsNone(vendor_entity_id)


if __name__ == "__main__":
    unittest.main()
