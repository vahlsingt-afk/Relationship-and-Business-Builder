"""
test_ownership_promotion.py — RB-2026-09-11.

Coverage for ownership_promotion.py, the review-first promotion engine
built to close a real gap: M&A/ownership-change signals are already
detected daily by multiple scanners, but no entity ever had a structured
owner/parent-company field to promote them into -- see the module's own
docstring for full context, including the real Del Taco/Yadav Enterprises
case this was built to close. Isolated from real production data
throughout (own tmp graph, own tmp candidate store, own tmp
account_intelligence dir), same pattern as
test_tech_stack_relationship_promotion.py.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"

spec = importlib.util.spec_from_file_location("ecosystem_intelligence", SCRIPTS_DIR / "ecosystem_intelligence.py")
ei = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ei)

spec2 = importlib.util.spec_from_file_location("ecosystem_brief", SCRIPTS_DIR / "ecosystem_brief.py")
eb = importlib.util.module_from_spec(spec2)
assert spec2.loader is not None
spec2.loader.exec_module(eb)

sys.modules["ecosystem_intelligence"] = ei
sys.modules["ecosystem_brief"] = eb
sys.path.insert(0, str(SCRIPTS_DIR))

spec3 = importlib.util.spec_from_file_location("ownership_promotion", SCRIPTS_DIR / "ownership_promotion.py")
op = importlib.util.module_from_spec(spec3)
assert spec3.loader is not None
spec3.loader.exec_module(op)


def _graph_with(entities=None, signals=None, sources=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-11",
        "domain_packs": ["restaurants"],
        "entities": entities or [], "relationships": [], "signals": signals or [],
        "sources": sources or [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _entity(entity_id, name, entity_type="brand", aliases=None):
    return {"id": entity_id, "name": name, "entity_type": entity_type, "status": "active", "aliases": aliases or [],
            "attributes": {}, "sources": [], "confidence": {"level": "high"}, "domains": ["restaurants"]}


DEL_TACO = _entity("brand-del-taco", "Del Taco")
YADAV = _entity("company-yadav-enterprises", "Yadav Enterprises", entity_type="vendor")


class _IsolatedGraphMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._store_path = tmp / "ownership_promotion_candidates.json"
        self._promoted_path = tmp / "ownership_promotion_promoted.json"
        self._ai_dir = tmp / "account_intelligence"
        self._ai_dir.mkdir(parents=True)

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_store_path = op.STORE_PATH
        self._orig_promoted_path = op.PROMOTED_PATH
        self._orig_ai_dir = op.ACCOUNT_INTELLIGENCE_DIR
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        op.STORE_PATH = self._store_path
        op.PROMOTED_PATH = self._promoted_path
        op.ACCOUNT_INTELLIGENCE_DIR = self._ai_dir

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        op.STORE_PATH = self._orig_store_path
        op.PROMOTED_PATH = self._orig_promoted_path
        op.ACCOUNT_INTELLIGENCE_DIR = self._orig_ai_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _write_doc(self, name: str, text: str) -> None:
        (self._ai_dir / name).write_text(text, encoding="utf-8")

    def _all_candidates(self) -> list[dict]:
        """Confidence-Based Auto-Recording Phase 6 (2026-09-25): scan()
        now auto-applies a candidate immediately whenever a real owner
        name was extracted, so pending_candidates() is empty right after
        such a scan -- tests inspecting what a scan just produced read the
        full candidate store instead."""
        return list(op._load_store()["candidates"].values())


class TestIsOwnershipSignal(unittest.TestCase):
    def test_acquisition_language_is_ownership(self):
        self.assertTrue(op._is_ownership_signal(
            "Del Taco Begins New Growth Era Following Acquisition by Yadav Enterprises"
        ))

    def test_plain_funding_round_is_not_ownership(self):
        self.assertFalse(op._is_ownership_signal("XYZ Raises $50M in Series B Funding"))
        self.assertFalse(op._is_ownership_signal("ABC Restaurant Group Closes $12M Investment Round"))


class TestExtractAcquirerName(unittest.TestCase):
    """Validated against this account's own two real Del Taco acquisition
    headlines and real account_intelligence prose (see module docstring)."""

    def test_extracts_from_real_2025_yadav_headline(self):
        name, confidence = op._extract_acquirer_name(
            "Del Taco",
            "Del Taco Begins New Growth Era Following Acquisition by Yadav Enterprises - Business Wire. "
            "Del Taco Begins New Growth Era Following Acquisition by Yadav Enterprises - Business Wire",
        )
        self.assertEqual(name, "Yadav Enterprises")
        self.assertEqual(confidence, "extracted_from_text")

    def test_extracts_from_real_2022_jack_in_the_box_headline(self):
        name, confidence = op._extract_acquirer_name(
            "Del Taco", "Jack in the Box Completes its Acquisition of Del Taco - Business Wire",
        )
        self.assertEqual(name, "Jack in the Box")
        self.assertEqual(confidence, "extracted_from_text")

    def test_extracts_from_real_owned_by_phrasing(self):
        name, confidence = op._extract_acquirer_name(
            "Del Taco",
            "Del Taco is now a Yadav Enterprises-owned, predominantly franchised Mexican QSR "
            "executing an 18-month post-acquisition turnaround called Project Sunrise.",
        )
        self.assertEqual(name, "Yadav Enterprises")
        self.assertEqual(confidence, "extracted_from_text")

    def test_extracts_from_real_acquired_from_phrasing(self):
        name, confidence = op._extract_acquirer_name(
            "Del Taco", "Yadav Enterprises acquired Del Taco from Jack in the Box in late 2025.",
        )
        self.assertEqual(name, "Yadav Enterprises")
        self.assertEqual(confidence, "extracted_from_text")

    def test_unrelated_leading_capitalized_phrase_is_rejected_not_over_captured(self):
        """RB-2026-09-11, confirmed live against real account_intelligence
        prose: an unrelated capitalized job-title phrase ahead of the real
        name got swept into the capture -- "Chief Transformation Officer
        supporting Yadav's Del Taco acquisition" instead of just "Yadav".
        _is_plausible_company_name() must discard these rather than
        propose a name-shaped-but-wrong guess."""
        name, confidence = op._extract_acquirer_name(
            "Del Taco",
            "Ulyses Camacho — Chief Transformation Officer supporting Yadav's Del Taco acquisition "
            "while continuing as Taco Cabana president and COO.",
        )
        self.assertIsNone(name)
        self.assertEqual(confidence, "unknown")

    def test_unrelated_leading_capitalized_sentence_starter_is_rejected(self):
        name, confidence = op._extract_acquirer_name(
            "Del Taco",
            "Because Yadav acquired Del Taco afterward, current rollout status requires validation.",
        )
        self.assertIsNone(name)
        self.assertEqual(confidence, "unknown")

    def test_no_match_returns_none_and_unknown(self):
        name, confidence = op._extract_acquirer_name(
            "Del Taco", "Del Taco Brings Back Fan-Favorite Fall Menu Item.",
        )
        self.assertIsNone(name)
        self.assertEqual(confidence, "unknown")


class TestIsNamedAsAcquirerOrSeller(unittest.TestCase):
    """RB-2026-09-29: real false positives found live in the ownership
    review -- an entity that is the ACQUIRER or SELLER in a sentence was
    getting its own "who owns this?" candidate created, exactly backwards.
    Each case here is one of the concrete headlines/lines that actually
    produced a wrong-direction candidate before this fix; the entity being
    tested is always the one that should NOT get a candidate, and the
    true target (tested separately where relevant) confirms the fix
    doesn't over-correct and start excluding real targets."""

    def test_active_acquirer_is_excluded(self):
        self.assertTrue(op._is_named_as_acquirer_or_seller(
            "Yadav Enterprises", "Yadav Enterprises acquired Del Taco from Jack in the Box in late 2025.",
        ))

    def test_true_target_of_active_acquirer_sentence_is_not_excluded(self):
        self.assertFalse(op._is_named_as_acquirer_or_seller(
            "Del Taco", "Yadav Enterprises acquired Del Taco from Jack in the Box in late 2025.",
        ))

    def test_seller_named_after_from_is_excluded(self):
        self.assertTrue(op._is_named_as_acquirer_or_seller(
            "Jack in the Box", "Yadav Enterprises acquired Del Taco from Jack in the Box in late 2025.",
        ))

    def test_seller_in_active_sale_to_phrasing_is_excluded(self):
        self.assertTrue(op._is_named_as_acquirer_or_seller(
            "Jack in the Box",
            "Jack in the Box officially completed the $119M sale to Yadav Enterprises on Dec. 22, 2025.",
        ))

    def test_acquirer_named_after_passive_by_is_excluded(self):
        self.assertTrue(op._is_named_as_acquirer_or_seller(
            "Dave & Buster's", "MAIN EVENT TO BE ACQUIRED BY DAVE & BUSTER'S FOR $835 MILLION - PR Newswire.",
        ))

    def test_true_target_of_passive_by_headline_is_not_excluded(self):
        self.assertFalse(op._is_named_as_acquirer_or_seller(
            "Main Event", "MAIN EVENT TO BE ACQUIRED BY DAVE & BUSTER'S FOR $835 MILLION - PR Newswire.",
        ))

    def test_acquirer_in_acquisition_of_phrasing_is_excluded(self):
        self.assertTrue(op._is_named_as_acquirer_or_seller(
            "Biscuit Belly",
            "Biscuit Belly Positions for Next Phase of Growth with Acquisition of Maple Street "
            "Biscuit Company - PR Newswire.",
        ))

    def test_true_target_of_acquisition_of_phrasing_is_not_excluded(self):
        self.assertFalse(op._is_named_as_acquirer_or_seller(
            "Maple Street Biscuit Company",
            "Biscuit Belly Positions for Next Phase of Growth with Acquisition of Maple Street "
            "Biscuit Company - PR Newswire.",
        ))

    def test_proposed_acquisition_of_is_excluded(self):
        self.assertTrue(op._is_named_as_acquirer_or_seller(
            "J. Alexander's",
            "Marathon Partners Pleased That Glass Lewis Has Joined ISS in Recommending Against "
            "J. Alexander's Proposed Acquisition of 99 Restaurants - PR Newswire.",
        ))

    def test_target_named_after_acquisition_of_is_not_excluded_even_when_acquirer_precedes_it(self):
        """Word order matters: the true target's name appearing AFTER
        "acquisition of" must not be excluded just because it also
        appears somewhere in a sentence that names the real acquirer
        first."""
        self.assertFalse(op._is_named_as_acquirer_or_seller(
            "J. Alexander's", "SPB Hospitality Completes Acquisition of J. Alexander's Holdings, Inc.",
        ))

    def test_plain_target_sentence_is_not_excluded(self):
        self.assertFalse(op._is_named_as_acquirer_or_seller(
            "Del Taco",
            "Del Taco is now a Yadav Enterprises-owned, predominantly franchised Mexican QSR "
            "executing an 18-month post-acquisition turnaround called Project Sunrise.",
        ))


class TestScanExistingSignals(_IsolatedGraphMixin, unittest.TestCase):
    def test_finds_real_ownership_shaped_funding_event(self):
        graph = _graph_with(
            entities=[DEL_TACO],
            signals=[{
                "id": "sig-2025-12-22-brand-del-taco-funding-event", "signal_type": "funding_event",
                "event_at": "2025-12-22",
                "summary": "Del Taco Begins New Growth Era Following Acquisition by Yadav Enterprises - Business Wire.",
                "entities": ["brand-del-taco"], "sources": [], "confidence": {"level": "high"},
            }],
        )
        self._write_graph(graph)
        result = op.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(result["auto_applied"], 1)
        candidates = self._all_candidates()
        self.assertEqual(candidates[0]["proposed_owner_name"], "Yadav Enterprises")
        self.assertEqual(candidates[0]["evidence_date"], "2025-12-22")
        self.assertEqual(candidates[0]["status"], "confirmed")
        self.assertEqual(candidates[0]["confirmed_by"], "system:ownership_promotion")
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
        self.assertEqual(entity["owner_name"], "Yadav Enterprises")

    def test_skips_plain_funding_round_signal(self):
        graph = _graph_with(
            entities=[DEL_TACO],
            signals=[{
                "id": "sig-2026-01-05-test-1", "signal_type": "funding_event", "event_at": "2026-01-05",
                "summary": "Del Taco Raises $10M in Growth Capital",
                "entities": ["brand-del-taco"], "sources": [], "confidence": {"level": "medium"},
            }],
        )
        self._write_graph(graph)
        result = op.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 0)

    def test_acquirer_tagged_on_the_same_signal_gets_no_candidate(self):
        """RB-2026-09-29: the real Main Event / Dave & Buster's signal --
        ecosystem_brief tags BOTH entities named in a funding_event
        summary, so a signal announcing Main Event's acquisition also
        lists Dave & Buster's (the acquirer, a separately tracked brand)
        in its own `entities` array. Before this fix, Dave & Buster's got
        its own wrong-direction "who owns this?" candidate from the exact
        same signal; only Main Event (the real target) should."""
        main_event = _entity("brand-main-event", "Main Event")
        dnb = _entity("brand-dave-busters", "Dave & Buster's")
        graph = _graph_with(
            entities=[main_event, dnb],
            signals=[{
                "id": "sig-2022-04-06-brand-main-event-funding-event", "signal_type": "funding_event",
                "event_at": "2022-04-06",
                "summary": "MAIN EVENT TO BE ACQUIRED BY DAVE & BUSTER'S FOR $835 MILLION - PR Newswire.",
                "entities": ["brand-main-event", "brand-dave-busters"], "sources": [], "confidence": {"level": "high"},
            }],
        )
        self._write_graph(graph)
        result = op.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 1)
        candidates = self._all_candidates()
        self.assertEqual(candidates[0]["entity_id"], "brand-main-event")

    def test_two_real_ownership_signals_both_become_separate_candidates(self):
        """The real Del Taco case: a 2022 Jack in the Box acquisition
        signal AND a 2025 Yadav Enterprises acquisition signal both exist
        for the same entity. Both must surface -- never auto-pick one as
        "the real" owner; that's the reviewer's call."""
        graph = _graph_with(
            entities=[DEL_TACO],
            signals=[
                {
                    "id": "sig-2022-03-08-brand-del-taco-funding-event", "signal_type": "funding_event",
                    "event_at": "2022-03-08",
                    "summary": "Jack in the Box Completes its Acquisition of Del Taco - Business Wire.",
                    "entities": ["brand-del-taco"], "sources": [], "confidence": {"level": "high"},
                },
                {
                    "id": "sig-2025-12-22-brand-del-taco-funding-event", "signal_type": "funding_event",
                    "event_at": "2025-12-22",
                    "summary": "Del Taco Begins New Growth Era Following Acquisition by Yadav Enterprises - Business Wire.",
                    "entities": ["brand-del-taco"], "sources": [], "confidence": {"level": "high"},
                },
            ],
        )
        self._write_graph(graph)
        result = op.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 2)
        owners = sorted(c["proposed_owner_name"] for c in self._all_candidates())
        self.assertEqual(owners, ["Jack in the Box", "Yadav Enterprises"])
        # Neither auto-resolves as "the" owner over the other -- the second
        # one processed lands in reported_alternates rather than silently
        # overwriting the first (same non-destructive principle as the
        # relationship conflict engine, applied here to two candidates
        # arriving in the same scan pass).
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
        self.assertIn(entity["owner_name"], ["Jack in the Box", "Yadav Enterprises"])

    def test_idempotent_rescan_does_not_duplicate(self):
        graph = _graph_with(
            entities=[DEL_TACO],
            signals=[{
                "id": "sig-2026-01-05-test-2", "signal_type": "funding_event", "event_at": "2026-01-05",
                "summary": "Del Taco Begins New Growth Era Following Acquisition by Yadav Enterprises.",
                "entities": ["brand-del-taco"], "sources": [], "confidence": {"level": "high"},
            }],
        )
        self._write_graph(graph)
        op.scan_existing_signals()
        result2 = op.scan_existing_signals()
        self.assertEqual(result2["new_candidates"], 0)
        self.assertEqual(len(self._all_candidates()), 1)


class TestScanAccountIntelligenceDocs(_IsolatedGraphMixin, unittest.TestCase):
    def test_finds_real_del_taco_ownership_line(self):
        graph = _graph_with(entities=[DEL_TACO])
        self._write_doc("2026-09-04-del-taco-executive-summary.md", (
            "Del Taco is now a Yadav Enterprises-owned, predominantly franchised Mexican QSR "
            "executing an 18-month post-acquisition turnaround called Project Sunrise.\n"
        ))
        self._write_graph(graph)
        result = op.scan_account_intelligence_docs()
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(result["auto_applied"], 1)
        candidates = self._all_candidates()
        self.assertEqual(candidates[0]["proposed_owner_name"], "Yadav Enterprises")
        self.assertEqual(candidates[0]["evidence_date"], "2026-09-04")

    def test_unrelated_line_produces_no_candidate(self):
        graph = _graph_with(entities=[DEL_TACO])
        self._write_doc("test-notes.md", "Del Taco is planning a new value menu for Q3.\n")
        self._write_graph(graph)
        result = op.scan_account_intelligence_docs()
        self.assertEqual(result["new_candidates"], 0)

    def test_vendor_entities_are_never_scanned(self):
        """RB-2026-09-29: live review of the pending queue found 34 of 145
        candidates were vendor entities swept up from ordinary payments/
        tech-stack vocabulary in account-intelligence notes ("protect the
        Worldpay gateway/acquiring position", "what does Fiserv own
        today") -- every one a false positive, none a real ownership
        event. This scanner is brand-only now; a vendor's own name sitting
        next to "acquiring"/"owned" in a sales-strategy note must never
        produce a candidate, even though the line would clearly trip the
        ownership-signal regex."""
        worldpay = _entity("vendor-worldpay", "Worldpay", entity_type="vendor")
        graph = _graph_with(entities=[worldpay])
        self._write_doc("2026-07-28-worldpay-cross-sell-strategy-background-draft.md", (
            "Lead with digital menu boards and protect the Worldpay gateway/acquiring position.\n"
        ))
        self._write_graph(graph)
        result = op.scan_account_intelligence_docs()
        self.assertEqual(result["new_candidates"], 0)
        self.assertEqual(self._all_candidates(), [])

    def test_brand_entity_still_scanned_alongside_excluded_vendor(self):
        """Companion to test_vendor_entities_are_never_scanned: confirms the
        brand-only filter is a real exclusion, not an accidental break of
        the scan entirely -- a brand on the same line as an excluded
        vendor name still produces its own candidate."""
        worldpay = _entity("vendor-worldpay", "Worldpay", entity_type="vendor")
        graph = _graph_with(entities=[DEL_TACO, worldpay])
        self._write_doc("2026-09-04-del-taco-executive-summary.md", (
            "Del Taco also has a high-confidence Worldpay payments relationship; Del Taco was "
            "acquired by Yadav Enterprises in late 2025.\n"
        ))
        self._write_graph(graph)
        result = op.scan_account_intelligence_docs()
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(self._all_candidates()[0]["entity_id"], "brand-del-taco")


class TestProposeOwnershipFinding(_IsolatedGraphMixin, unittest.TestCase):
    def test_owner_name_supplied_directly(self):
        graph = _graph_with(entities=[DEL_TACO])
        self._write_graph(graph)
        result = op.propose_ownership_finding(
            "brand-del-taco", evidence_text="Confirmed via SEC filing: Yadav Enterprises closed the acquisition.",
            owner_name="Yadav Enterprises",
        )
        self.assertTrue(result["proposed"])
        pending = op.pending_candidates()
        self.assertEqual(pending[0]["proposed_owner_name"], "Yadav Enterprises")
        self.assertEqual(pending[0]["proposed_owner_confidence"], "stated_by_caller")

    def test_unknown_entity_errors(self):
        graph = _graph_with(entities=[])
        self._write_graph(graph)
        result = op.propose_ownership_finding("brand-does-not-exist", evidence_text="some evidence")
        self.assertIn("error", result)

    def test_empty_evidence_rejected(self):
        graph = _graph_with(entities=[DEL_TACO])
        self._write_graph(graph)
        result = op.propose_ownership_finding("brand-del-taco", evidence_text="   ")
        self.assertIn("error", result)

    def test_invalid_evidence_date_rejected(self):
        graph = _graph_with(entities=[DEL_TACO])
        self._write_graph(graph)
        result = op.propose_ownership_finding(
            "brand-del-taco", evidence_text="Del Taco was acquired.", evidence_date="not-a-date",
        )
        self.assertIn("error", result)


class TestRecordProposal(_IsolatedGraphMixin, unittest.TestCase):
    def _seed_pending(self, proposed_owner_name=None, entities=None):
        graph = _graph_with(entities=entities if entities is not None else [DEL_TACO])
        self._write_graph(graph)
        store = {"candidates": {
            "brand-del-taco::sig-test": {
                "candidate_id": "brand-del-taco::sig-test", "status": "proposed_pending_confirmation",
                "entity_id": "brand-del-taco", "entity_name": "Del Taco",
                "proposed_owner_name": proposed_owner_name,
                "proposed_owner_confidence": "extracted_from_text" if proposed_owner_name else "unknown",
                "source_type": "credible_reporting", "source_title": "test", "source_url": None,
                "evidence_excerpt": "Del Taco was acquired.", "origin": "signal_scan", "origin_ref": "sig-test",
                "evidence_date": "2025-12-22", "detected_at": "2026-09-11T00:00:00Z", "resolved_at": None,
            }
        }}
        op._save_store(store)

    def test_confirm_writes_owner_onto_the_real_entity(self):
        self._seed_pending(proposed_owner_name="Yadav Enterprises")
        result = op.record_proposal("brand-del-taco::sig-test", confirmed=True)
        self.assertTrue(result["confirmed"])
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
        self.assertEqual(entity["owner_name"], "Yadav Enterprises")
        self.assertIsNone(entity["owner_entity_id"])  # Yadav not itself a tracked entity here

    def test_confirm_resolves_owner_entity_id_when_owner_is_tracked(self):
        self._seed_pending(proposed_owner_name="Yadav Enterprises", entities=[DEL_TACO, YADAV])
        op.record_proposal("brand-del-taco::sig-test", confirmed=True)
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
        self.assertEqual(entity["owner_entity_id"], "company-yadav-enterprises")

    def test_confirm_without_any_owner_name_is_blocked(self):
        self._seed_pending(proposed_owner_name=None)
        result = op.record_proposal("brand-del-taco::sig-test", confirmed=True)
        self.assertIn("error", result)
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
        self.assertNotIn("owner_name", entity)

    def test_confirm_with_explicit_owner_name_overrides_prefilled_one(self):
        """Reviewer correcting a wrong-direction extraction."""
        self._seed_pending(proposed_owner_name="Wrong Company")
        result = op.record_proposal("brand-del-taco::sig-test", confirmed=True, owner_name="Yadav Enterprises")
        self.assertEqual(result["owner_name"], "Yadav Enterprises")

    def test_auto_apply_gate_overwrites_when_no_existing_owner(self):
        """Confidence-Based Auto-Recording Phase 6: a pure add (no
        owner_name on file yet) always applies, gate or no gate."""
        self._seed_pending(proposed_owner_name="Yadav Enterprises")
        result = op.record_proposal("brand-del-taco::sig-test", confirmed=True,
                                     confirmed_by="system:ownership_promotion", auto_apply_gate=True)
        self.assertEqual(result["outcome"], "applied")
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
        self.assertEqual(entity["owner_name"], "Yadav Enterprises")
        self.assertEqual(entity["owner_confirmed_by"], "system:ownership_promotion")

    def test_auto_apply_gate_defers_to_reported_alternates_on_weak_claim(self):
        """A DIFFERENT owner_name already on file, no stored owner_
        confidence (defaults to "high"), and a low-confidence source_type
        on the new claim: gated auto-apply must not overwrite."""
        del_taco_owned = dict(DEL_TACO, owner_name="Jack in the Box")
        self._seed_pending(proposed_owner_name="Yadav Enterprises", entities=[del_taco_owned])
        result = op.record_proposal("brand-del-taco::sig-test", confirmed=True,
                                     confirmed_by="system:ownership_promotion", auto_apply_gate=True)
        self.assertEqual(result["outcome"], "recorded_alongside")
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
        self.assertEqual(entity["owner_name"], "Jack in the Box")
        self.assertEqual(len(entity["reported_alternates"]), 1)
        self.assertEqual(entity["reported_alternates"][0]["owner_name"], "Yadav Enterprises")

    def test_explicit_confirm_without_gate_overwrites_regardless_of_confidence(self):
        """The original, unchanged behavior for a direct human confirm
        (auto_apply_gate=False, the default) -- must NOT be redirected to
        reported_alternates just because the source_type is weak."""
        del_taco_owned = dict(DEL_TACO, owner_name="Jack in the Box")
        self._seed_pending(proposed_owner_name="Yadav Enterprises", entities=[del_taco_owned])
        result = op.record_proposal("brand-del-taco::sig-test", confirmed=True)
        self.assertEqual(result["outcome"], "applied")
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
        self.assertEqual(entity["owner_name"], "Yadav Enterprises")
        self.assertNotIn("reported_alternates", entity)

    def test_reject_writes_nothing(self):
        self._seed_pending(proposed_owner_name="Yadav Enterprises")
        before = self._graph_path.read_text()
        result = op.record_proposal("brand-del-taco::sig-test", confirmed=False)
        self.assertTrue(result["rejected"])
        self.assertEqual(self._graph_path.read_text(), before)

    def test_cannot_resolve_twice(self):
        self._seed_pending(proposed_owner_name="Yadav Enterprises")
        op.record_proposal("brand-del-taco::sig-test", confirmed=True)
        result = op.record_proposal("brand-del-taco::sig-test", confirmed=True)
        self.assertIn("error", result)

    def test_unknown_candidate_id_errors(self):
        result = op.record_proposal("not-a-real-id", confirmed=True)
        self.assertIn("error", result)


class TestSameDayPromotion(_IsolatedGraphMixin, unittest.TestCase):
    """RB-2026-09-18, next-sprint Workstream 1: a candidate detected by
    today's scan() (or propose_ownership_finding()) must be visible via
    same_day_candidates() the same day -- purely additive visibility,
    independent of record_proposal()'s confirm-before-mutate gate."""

    def _graph(self):
        return _graph_with(
            entities=[DEL_TACO],
            signals=[{
                "id": "sig-2025-12-22-sameday-1", "signal_type": "funding_event", "event_at": "2025-12-22",
                "summary": "Del Taco Begins New Growth Era Following Acquisition by Yadav Enterprises - Business Wire.",
                "entities": ["brand-del-taco"], "sources": [], "confidence": {"level": "high"},
            }],
        )

    def test_scan_promotes_new_candidate_same_day(self):
        self._write_graph(self._graph())
        op.scan()
        same_day = op.same_day_candidates()
        self.assertEqual(len(same_day), 1)
        self.assertEqual(same_day[0]["entity_name"], "Del Taco")
        self.assertEqual(same_day[0]["proposed_owner_name"], "Yadav Enterprises")

    def test_rescan_does_not_duplicate_promoted_entry(self):
        self._write_graph(self._graph())
        op.scan()
        op.scan()
        self.assertEqual(len(op.same_day_candidates()), 1)

    def test_stale_manifest_from_a_prior_day_is_not_returned(self):
        self._promoted_path.write_text(json.dumps({
            "_scan_date": "2026-09-01",
            "candidates": [{"candidate_id": "x", "entity_name": "Stale Corp"}],
        }), encoding="utf-8")
        self.assertEqual(op.same_day_candidates(), [])

    def test_dry_run_scan_does_not_promote(self):
        self._write_graph(self._graph())
        op.scan(dry_run=True)
        self.assertFalse(self._promoted_path.exists())

    def test_no_new_candidates_leaves_manifest_untouched(self):
        self._write_graph(_graph_with(
            entities=[DEL_TACO],
            signals=[{
                "id": "sig-2026-01-05-sameday-2", "signal_type": "funding_event", "event_at": "2026-01-05",
                "summary": "Del Taco Raises $10M in Growth Capital",
                "entities": ["brand-del-taco"], "sources": [], "confidence": {"level": "medium"},
            }],
        ))
        op.scan()
        self.assertFalse(self._promoted_path.exists())

    def test_propose_ownership_finding_also_promotes_same_day(self):
        """The external-research path (rbb-chat/Codex sourced) is a real,
        deliberate single-candidate creation -- same-day visibility applies
        here too, not just to the daily batch scan()."""
        self._write_graph(_graph_with(entities=[DEL_TACO]))
        result = op.propose_ownership_finding(
            "brand-del-taco", evidence_text="Del Taco is now owned by Example Capital Partners.",
            source_url="https://example.com/story",
        )
        self.assertTrue(result["proposed"])
        same_day = op.same_day_candidates()
        self.assertEqual(len(same_day), 1)
        self.assertEqual(same_day[0]["proposed_owner_name"], "Example Capital Partners")


if __name__ == "__main__":
    unittest.main()
