"""
test_executive_move_promotion.py — RB-2026-09-11.

Coverage for executive_move_promotion.py, the review-first promotion
engine built to close a real gap: leadership_change signals are RBB's most
common real material signal type, but the named executive was discarded
at classification time -- never checked against baseline_index.json,
RBB's real contact-tracking system. See the module's own docstring for
full context, including the real Church's Texas Chicken/Roland Gonzalez
case and the real Crumbl/ezCater mis-classification this was built to
work around. Isolated from real production data throughout (own tmp
graph, own tmp candidate store, own tmp baseline, own tmp
account_intelligence dir, mocked schema-validator subprocess -- same
isolation pattern as test_tech_stack_relationship_promotion.py).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"

spec = importlib.util.spec_from_file_location("ecosystem_intelligence", SCRIPTS_DIR / "ecosystem_intelligence.py")
ei = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ei)
sys.modules["ecosystem_intelligence"] = ei
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402
import mutations  # noqa: E402
import thread_promotion  # noqa: E402
import identity_match_review as imr  # noqa: E402

spec2 = importlib.util.spec_from_file_location("executive_move_promotion", SCRIPTS_DIR / "executive_move_promotion.py")
emp = importlib.util.module_from_spec(spec2)
assert spec2.loader is not None
spec2.loader.exec_module(emp)


def _graph_with(entities=None, signals=None, sources=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-11",
        "entities": entities or [], "relationships": [], "signals": signals or [],
        "sources": sources or [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _entity(entity_id, name, entity_type="brand"):
    return {"id": entity_id, "name": name, "entity_type": entity_type, "aliases": [],
            "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}


DAIRY_QUEEN = _entity("brand-dairy-queen", "Dairy Queen")
BLACK_BEAR = _entity("brand-black-bear-diner", "Black Bear Diner")
CRUMBL = _entity("brand-crumbl", "Crumbl")


def _contact(contact_id, name, company=None, role=None):
    return {
        "id": contact_id, "name": name, "signal_class": "LMI", "sources": ["linkedin_export_test"],
        "current_company": company, "current_role": role, "email": None, "phone": None,
    }


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._store_path = tmp / "executive_move_candidates.json"
        self._promoted_path = tmp / "executive_move_promoted.json"
        self._baseline_path = tmp / "baseline_index.json"
        self._snap_dir = tmp / "_snapshots"
        self._ai_dir = tmp / "account_intelligence"
        self._ai_dir.mkdir(parents=True)
        self._baseline_path.write_text("[]", encoding="utf-8")

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_store_path = emp.STORE_PATH
        self._orig_promoted_path = emp.PROMOTED_PATH
        self._orig_ai_dir = emp.ACCOUNT_INTELLIGENCE_DIR
        self._orig_baseline_path = core.BASELINE_PATH
        self._orig_snap_dir = core.SNAPSHOTS_DIR
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        emp.STORE_PATH = self._store_path
        emp.PROMOTED_PATH = self._promoted_path
        emp.ACCOUNT_INTELLIGENCE_DIR = self._ai_dir
        core.BASELINE_PATH = self._baseline_path
        core.SNAPSHOTS_DIR = self._snap_dir

        self._patch_validator = unittest.mock.patch("mutations.subprocess.run")
        mock_run = self._patch_validator.start()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

    def tearDown(self):
        self._patch_validator.stop()
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        emp.STORE_PATH = self._orig_store_path
        emp.PROMOTED_PATH = self._orig_promoted_path
        emp.ACCOUNT_INTELLIGENCE_DIR = self._orig_ai_dir
        core.BASELINE_PATH = self._orig_baseline_path
        core.SNAPSHOTS_DIR = self._orig_snap_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _write_baseline(self, contacts: list[dict]) -> None:
        self._baseline_path.write_text(json.dumps(contacts), encoding="utf-8")

    def _write_doc(self, name: str, text: str) -> None:
        (self._ai_dir / name).write_text(text, encoding="utf-8")

    def _all_candidates(self) -> list[dict]:
        """Confidence-Based Auto-Recording Phase 6 (2026-09-25): scan()
        now auto-applies a candidate immediately whenever a real name was
        extracted, so pending_candidates() is empty right after such a
        scan -- tests inspecting what a scan just produced read the full
        candidate store instead."""
        return list(emp._load_store()["candidates"].values())


class TestIsLeadershipAppointmentSignal(unittest.TestCase):
    def test_real_appointment_language_is_material(self):
        self.assertTrue(emp._is_leadership_appointment_signal("Dairy Queen names Phil Crawford chief technology officer."))
        self.assertTrue(emp._is_leadership_appointment_signal("Papa Johns' CFO to depart."))

    def test_real_mistagged_signal_is_rejected(self):
        """RB-2026-09-11, confirmed live: a real signal in the production
        graph carries signal_type=leadership_change with zero leadership
        content -- a plain partnership press release."""
        self.assertFalse(emp._is_leadership_appointment_signal(
            "Crumbl Partners With ezCater to Bring Its Iconic Cookies to Workplaces - Business Wire."
        ))

    def test_bare_title_mention_is_rejected(self):
        self.assertFalse(emp._is_leadership_appointment_signal(
            "Church's Texas Chicken CEO says new funding will boost remodels, expansion."
        ))


class TestExtractExecutive(unittest.TestCase):
    """Validated against real confirmed leadership_change signals in the
    live production graph (see module docstring)."""

    def test_extracts_from_real_names_headline(self):
        name, title, confidence = emp._extract_executive(
            "Dairy Queen", "Dairy Queen names Phil Crawford chief technology officer.",
        )
        self.assertEqual(name, "Phil Crawford")
        self.assertEqual(title, "chief technology officer")
        self.assertEqual(confidence, "extracted_from_text")

    def test_extracts_from_real_promoted_to_headline(self):
        name, title, confidence = emp._extract_executive(
            "Black Bear Diner", "Anita Adams promoted to CEO of Black Bear Diner - Nation's Restaurant News",
        )
        self.assertEqual(name, "Anita Adams")
        self.assertEqual(title, "CEO")
        self.assertEqual(confidence, "extracted_from_text")

    def test_extracts_from_real_all_caps_headline(self):
        name, title, confidence = emp._extract_executive(
            "P.F. Chang's",
            "P.F. CHANG'S NAMES PATRICK BENSON DIRECTOR OF MARKETING COMMUNICATIONS - PR Newswire.",
        )
        self.assertEqual(name, "PATRICK BENSON")
        self.assertEqual(title, "DIRECTOR OF MARKETING COMMUNICATIONS")

    def test_no_name_present_returns_none(self):
        """Real confirmed case: "Golden Corral appoints 7-Eleven vet as
        CIO" names no real person -- must never fabricate one."""
        name, title, confidence = emp._extract_executive(
            "Golden Corral", "Golden Corral appoints 7-Eleven vet as CIO - Restaurant Dive",
        )
        self.assertIsNone(name)
        self.assertEqual(confidence, "unknown")

    def test_no_match_for_unrelated_text(self):
        name, title, confidence = emp._extract_executive(
            "Dairy Queen", "Dairy Queen brings back the Blizzard of the Month.",
        )
        self.assertIsNone(name)
        self.assertEqual(confidence, "unknown")

    def test_title_shaped_phrase_rejected_not_mistaken_for_a_name(self):
        """RB-2026-09-11, confirmed live against real production data:
        "NOTHING BUNDT CAKES NAMES NEW CEO" has no actual person name at
        all -- "New CEO" is a title phrase, not a name -- but the raw
        2-word-capitalized-word capture matched it anyway. Must be
        rejected, not proposed as a fabricated "name"."""
        name, title, confidence = emp._extract_executive(
            "Nothing Bundt Cakes", "NOTHING BUNDT CAKES NAMES NEW CEO - PR Newswire.",
        )
        self.assertIsNone(name)
        self.assertEqual(confidence, "unknown")

    def test_descriptor_phrase_rejected_not_mistaken_for_a_name(self):
        """Real confirmed case: "WaBa Grill Names Seasoned Restaurant
        Operator Afshin Compani to President..." -- the real name
        (Afshin Compani) sits further into the sentence than this
        module's patterns reach; "Seasoned Restaurant" (the descriptor
        immediately after "Names") must not be mistaken for it."""
        name, title, confidence = emp._extract_executive(
            "WaBa Grill",
            "WaBa Grill Names Seasoned Restaurant Operator Afshin Compani to President - PR Newswire.",
        )
        self.assertIsNone(name)
        self.assertEqual(confidence, "unknown")

    def test_extracts_past_tense_appointed_not_just_present_tense_appoints(self):
        """Real confirmed case, found in account_intelligence notes:
        "McDonald's appointed Skye Anderson as president..." -- past
        tense "appointed" must match, not just present-tense "appoints"."""
        name, title, confidence = emp._extract_executive(
            "McDonald's",
            "McDonald's appointed Skye Anderson as president of McDonald's USA after the previous president departed.",
        )
        self.assertEqual(name, "Skye Anderson")
        self.assertEqual(confidence, "extracted_from_text")

    def test_extracts_from_real_em_dash_bullet_convention(self):
        """Real confirmed case: Todd's own account_intelligence notes use
        a "- **Name — Title.**" bullet convention for known executives."""
        name, title, confidence = emp._extract_executive(
            "Worldpay", "- **Zerrick Pearson — Chief Information Officer.** Current 2026 executive-directory listing.",
        )
        self.assertEqual(name, "Zerrick Pearson")
        self.assertEqual(title, "Chief Information Officer")


class TestScanExistingSignals(_IsolatedFixtureMixin):
    def test_finds_real_appointment_and_extracts_name(self):
        graph = _graph_with(
            entities=[DAIRY_QUEEN],
            signals=[{
                "id": "sig-test-1", "signal_type": "leadership_change", "event_at": "2026-01-10",
                "summary": "Dairy Queen names Phil Crawford chief technology officer.",
                "entities": ["brand-dairy-queen"], "sources": [],
            }],
        )
        self._write_graph(graph)
        self._write_baseline([])
        result = emp.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(result["auto_applied"], 1)
        candidates = self._all_candidates()
        self.assertEqual(candidates[0]["proposed_name"], "Phil Crawford")
        self.assertEqual(candidates[0]["action"], "create_new_contact")
        self.assertIsNone(candidates[0]["matched_contact_id"])
        self.assertEqual(candidates[0]["status"], "confirmed")
        self.assertEqual(candidates[0]["confirmed_by"], "system:executive_move_promotion")
        baseline = json.loads(self._baseline_path.read_text())
        self.assertEqual(baseline[0]["name"], "Phil Crawford")
        self.assertEqual(baseline[0]["current_company"], "Dairy Queen")

    def test_skips_real_mistagged_signal(self):
        graph = _graph_with(
            entities=[CRUMBL],
            signals=[{
                "id": "sig-test-2", "signal_type": "leadership_change",
                "summary": "Crumbl Partners With ezCater to Bring Its Iconic Cookies to Workplaces - Business Wire.",
                "entities": ["brand-crumbl"], "sources": [],
            }],
        )
        self._write_graph(graph)
        self._write_baseline([])
        result = emp.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 0)

    def test_matches_an_existing_contact_by_name(self):
        graph = _graph_with(
            entities=[BLACK_BEAR],
            signals=[{
                "id": "sig-test-3", "signal_type": "leadership_change",
                "summary": "Anita Adams promoted to CEO of Black Bear Diner.",
                "entities": ["brand-black-bear-diner"], "sources": [],
            }],
        )
        self._write_graph(graph)
        self._write_baseline([_contact("anita-adams", "Anita Adams", company="Some Other Chain")])
        result = emp.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 1)
        candidates = self._all_candidates()
        self.assertEqual(candidates[0]["action"], "update_existing_contact")
        self.assertEqual(candidates[0]["matched_contact_id"], "anita-adams")
        # A DIFFERENT current_company already on file, with no stored
        # employment_confidence (defaults to "high" trust) and a
        # low-confidence unnamed source_type on the new claim: the auto-
        # apply loop must not silently overwrite -- it's recorded to
        # reported_alternates instead, leaving the contact record as-is.
        self.assertEqual(candidates[0]["status"], "confirmed")
        self.assertEqual(candidates[0]["outcome"], "recorded_alongside")
        baseline = json.loads(self._baseline_path.read_text())
        contact = next(c for c in baseline if c["id"] == "anita-adams")
        self.assertEqual(contact["current_company"], "Some Other Chain")
        self.assertEqual(contact["reported_alternates"][0]["current_company"], "Black Bear Diner")

    def test_idempotent_rescan_does_not_duplicate(self):
        graph = _graph_with(
            entities=[DAIRY_QUEEN],
            signals=[{
                "id": "sig-test-4", "signal_type": "leadership_change",
                "summary": "Dairy Queen names Phil Crawford chief technology officer.",
                "entities": ["brand-dairy-queen"], "sources": [],
            }],
        )
        self._write_graph(graph)
        self._write_baseline([])
        emp.scan_existing_signals()
        result2 = emp.scan_existing_signals()
        self.assertEqual(result2["new_candidates"], 0)
        self.assertEqual(len(self._all_candidates()), 1)


class TestScanAccountIntelligenceDocs(_IsolatedFixtureMixin):
    def test_finds_appointment_language_in_notes(self):
        graph = _graph_with(entities=[DAIRY_QUEEN])
        self._write_graph(graph)
        self._write_baseline([])
        self._write_doc("2026-09-11-notes.md", "Dairy Queen names Phil Crawford chief technology officer.\n")
        result = emp.scan_account_intelligence_docs()
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(result["auto_applied"], 1)
        self.assertEqual(self._all_candidates()[0]["proposed_name"], "Phil Crawford")

    def test_unrelated_line_produces_no_candidate(self):
        graph = _graph_with(entities=[DAIRY_QUEEN])
        self._write_graph(graph)
        self._write_baseline([])
        self._write_doc("test-notes.md", "Dairy Queen is planning a new Blizzard flavor for Q3.\n")
        result = emp.scan_account_intelligence_docs()
        self.assertEqual(result["new_candidates"], 0)


class TestRecordProposal(_IsolatedFixtureMixin):
    def _seed_pending(self, proposed_name=None, matched_contact_id=None):
        self._write_graph(_graph_with(entities=[DAIRY_QUEEN]))
        store = {"candidates": {"brand-dairy-queen::sig-test": {
            "candidate_id": "brand-dairy-queen::sig-test", "status": "proposed_pending_confirmation",
            "entity_id": "brand-dairy-queen", "entity_name": "Dairy Queen",
            "proposed_name": proposed_name, "proposed_title": "chief technology officer",
            "proposed_confidence": "extracted_from_text" if proposed_name else "unknown",
            "action": "update_existing_contact" if matched_contact_id else "create_new_contact",
            "matched_contact_id": matched_contact_id,
            "source_type": "credible_reporting", "source_title": "test", "source_url": None,
            "evidence_excerpt": "Dairy Queen names Phil Crawford chief technology officer.",
            "origin": "signal_scan", "origin_ref": "sig-test", "evidence_date": "2026-01-10",
            "detected_at": "2026-09-11T00:00:00Z", "resolved_at": None,
        }}}
        emp._save_store(store)

    def test_confirm_creates_new_contact_when_no_match(self):
        self._write_baseline([])
        self._seed_pending(proposed_name="Phil Crawford")
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["action"], "create_new_contact")
        baseline = json.loads(self._baseline_path.read_text())
        self.assertEqual(len(baseline), 1)
        self.assertEqual(baseline[0]["name"], "Phil Crawford")
        self.assertEqual(baseline[0]["current_company"], "Dairy Queen")
        self.assertEqual(baseline[0]["current_role"], "chief technology officer")
        self.assertEqual(baseline[0]["email"], None)
        self.assertEqual(baseline[0]["signal_class"], "VC")

    def test_confirm_updates_existing_contact_when_matched(self):
        self._write_baseline([_contact("phil-crawford", "Phil Crawford", company="Old Employer", role="Old Title")])
        self._seed_pending(proposed_name="Phil Crawford", matched_contact_id="phil-crawford")
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["action"], "update_existing_contact")
        baseline = json.loads(self._baseline_path.read_text())
        self.assertEqual(len(baseline), 1)  # no duplicate contact created
        self.assertEqual(baseline[0]["current_company"], "Dairy Queen")
        self.assertEqual(baseline[0]["current_role"], "chief technology officer")

    def test_confirm_without_any_name_is_blocked(self):
        self._write_baseline([])
        self._seed_pending(proposed_name=None)
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True)
        self.assertIn("error", result)
        baseline = json.loads(self._baseline_path.read_text())
        self.assertEqual(baseline, [])

    def test_confirm_with_explicit_contact_id_overrides_match(self):
        self._write_baseline([
            _contact("wrong-person", "Wrong Person"),
            _contact("phil-crawford", "Phil Crawford"),
        ])
        self._seed_pending(proposed_name="Phil Crawford", matched_contact_id="wrong-person")
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True, contact_id="phil-crawford")
        self.assertEqual(result["contact_id"], "phil-crawford")
        baseline = json.loads(self._baseline_path.read_text())
        updated = next(c for c in baseline if c["id"] == "phil-crawford")
        self.assertEqual(updated["current_company"], "Dairy Queen")
        wrong = next(c for c in baseline if c["id"] == "wrong-person")
        self.assertIsNone(wrong.get("current_company"))

    def test_reject_writes_nothing(self):
        self._write_baseline([])
        self._seed_pending(proposed_name="Phil Crawford")
        before = self._baseline_path.read_text()
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=False)
        self.assertTrue(result["rejected"])
        self.assertEqual(self._baseline_path.read_text(), before)

    def test_auto_apply_gate_overwrites_when_no_existing_company(self):
        """Confidence-Based Auto-Recording Phase 6: a pure add (contact
        exists but has no current_company on file) always applies, gate or
        no gate -- there's nothing to protect."""
        self._write_baseline([_contact("phil-crawford", "Phil Crawford", company=None)])
        self._seed_pending(proposed_name="Phil Crawford", matched_contact_id="phil-crawford")
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True,
                                      confirmed_by="system:executive_move_promotion", auto_apply_gate=True)
        self.assertEqual(result["outcome"], "applied")
        baseline = json.loads(self._baseline_path.read_text())
        self.assertEqual(baseline[0]["current_company"], "Dairy Queen")
        self.assertEqual(baseline[0]["current_company_confirmed_by"], "system:executive_move_promotion")

    def test_auto_apply_gate_defers_to_reported_alternates_on_weak_claim(self):
        """A DIFFERENT current_company already on file, no stored
        employment_confidence (defaults to "high"), and a low-confidence
        source_type on the new claim: gated auto-apply must not overwrite."""
        self._write_baseline([_contact("phil-crawford", "Phil Crawford", company="Old Employer")])
        self._seed_pending(proposed_name="Phil Crawford", matched_contact_id="phil-crawford")
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True,
                                      confirmed_by="system:executive_move_promotion", auto_apply_gate=True)
        self.assertEqual(result["outcome"], "recorded_alongside")
        baseline = json.loads(self._baseline_path.read_text())
        self.assertEqual(baseline[0]["current_company"], "Old Employer")
        self.assertEqual(len(baseline[0]["reported_alternates"]), 1)
        self.assertEqual(baseline[0]["reported_alternates"][0]["current_company"], "Dairy Queen")

    def test_explicit_confirm_without_gate_overwrites_regardless_of_confidence(self):
        """The original, unchanged behavior for a direct human confirm
        (auto_apply_gate=False, the default) -- must NOT be redirected to
        reported_alternates just because the source_type is weak."""
        self._write_baseline([_contact("phil-crawford", "Phil Crawford", company="Old Employer")])
        self._seed_pending(proposed_name="Phil Crawford", matched_contact_id="phil-crawford")
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True)
        self.assertEqual(result["outcome"], "applied")
        self.assertEqual(result["action"], "update_existing_contact")
        baseline = json.loads(self._baseline_path.read_text())
        self.assertEqual(baseline[0]["current_company"], "Dairy Queen")
        self.assertNotIn("reported_alternates", baseline[0])

    def test_cannot_resolve_twice(self):
        self._write_baseline([])
        self._seed_pending(proposed_name="Phil Crawford")
        emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True)
        result = emp.record_proposal("brand-dairy-queen::sig-test", confirmed=True)
        self.assertIn("error", result)

    def test_unknown_candidate_id_errors(self):
        result = emp.record_proposal("not-a-real-id", confirmed=True)
        self.assertIn("error", result)


class TestSameDayPromotion(_IsolatedFixtureMixin):
    """RB-2026-09-18, next-sprint Workstream 1: a candidate detected by
    today's scan() must be visible via same_day_candidates() the same day --
    purely additive visibility, independent of record_proposal()'s
    confirm-before-mutate gate (never asserted here as covered elsewhere)."""

    def _graph(self):
        return _graph_with(
            entities=[DAIRY_QUEEN],
            signals=[{
                "id": "sig-sameday-1", "signal_type": "leadership_change", "event_at": "2026-09-18",
                "summary": "Dairy Queen names Phil Crawford chief technology officer.",
                "entities": ["brand-dairy-queen"], "sources": [],
            }],
        )

    def test_scan_promotes_new_candidate_same_day(self):
        self._write_graph(self._graph())
        self._write_baseline([])
        emp.scan()
        same_day = emp.same_day_candidates()
        self.assertEqual(len(same_day), 1)
        self.assertEqual(same_day[0]["entity_name"], "Dairy Queen")
        self.assertEqual(same_day[0]["proposed_name"], "Phil Crawford")

    def test_rescan_does_not_duplicate_promoted_entry(self):
        self._write_graph(self._graph())
        self._write_baseline([])
        emp.scan()
        emp.scan()
        self.assertEqual(len(emp.same_day_candidates()), 1)

    def test_stale_manifest_from_a_prior_day_is_not_returned(self):
        self._promoted_path.write_text(json.dumps({
            "_scan_date": "2026-09-01",
            "candidates": [{"candidate_id": "x", "entity_name": "Stale Corp"}],
        }), encoding="utf-8")
        self.assertEqual(emp.same_day_candidates(), [])

    def test_dry_run_scan_does_not_promote(self):
        self._write_graph(self._graph())
        self._write_baseline([])
        emp.scan(dry_run=True)
        self.assertFalse(self._promoted_path.exists())
        self.assertEqual(emp.same_day_candidates(), [])

    def test_no_new_candidates_leaves_manifest_untouched(self):
        """A scan that finds nothing new (e.g. an unrelated/mistagged signal)
        must not write an empty dated manifest -- same_day_candidates() has
        nothing genuinely new to report, so there's nothing to surface."""
        self._write_graph(_graph_with(
            entities=[CRUMBL],
            signals=[{
                "id": "sig-sameday-2", "signal_type": "leadership_change",
                "summary": "Crumbl Partners With ezCater to Bring Its Iconic Cookies to Workplaces - Business Wire.",
                "entities": ["brand-crumbl"], "sources": [],
            }],
        ))
        self._write_baseline([])
        emp.scan()
        self.assertFalse(self._promoted_path.exists())


if __name__ == "__main__":
    unittest.main()
