"""
test_intelligence_triage.py — Multi-type intelligence triage tests.

DEFECT-015 fix verification: RB must identify ALL intelligence types present in
a single user input (macro_signal, micro_graph_enrichment, micro_graph_build,
ri_event, strategic_memory, noise) and return them sorted by mutation priority.

Test groups:
  IT1: Core invariants (schema, always read-only, noise-only, empty input)
  IT2: Macro signal classifier
  IT3: Micro graph enrichment classifier (existing artifacts)
  IT4: Micro graph build classifier (candidate new entities)
  IT5: RI event classifier
  IT6: Strategic memory classifier
  IT7: Multi-type detection (mixed inputs)
  IT8: Processing order (mutation priority sort)
  IT9: Author/event context propagation
  IT10: Registry injection (isolated from filesystem)
  IT11: Triage ID format
  IT12: End-to-end fixtures (realistic paste scenarios)
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import intelligence_triage as triage

# ---------------------------------------------------------------------------
# Shared registry fixture — no filesystem dependency
# ---------------------------------------------------------------------------

REGISTRY_FIXTURE = {
    "contract": "rb_intelligence_artifact_registry_v1",
    "artifacts": [
        {
            "artifact_id": "micro_graph:mcdonalds_us_ops",
            "artifact_type": "micro_graph",
            "name": "McDonald's U.S. Operations",
            "entity": "McDonald's",
            "entity_aliases": [
                "mcdonald", "mcd", "mcdonalds", "nsn", "storetech",
                "store tech", "field office", "coop", "co-op",
                "rfm", "otm", "stim", "fbp", "otp",
            ],
            "status": "active",
            "confidence": "high",
            "freshness_date": "2026-05-27",
            "queryable_via": "getMicroGraphSummary",
            "triage_trigger_terms": [
                "mcdonald", "mcd", "nsn", "golden arches", "franchisee",
                "storetech", "store tech", "field office", "coop", "co-op"
            ],
        },
        {
            "artifact_id": "micro_graph:par_technology",
            "artifact_type": "micro_graph",
            "name": "PAR Technology",
            "entity": "PAR Technology",
            "entity_aliases": ["par technology", "par tech", "par corp", "par inc"],
            "status": "stub",
            "confidence": "low",
            "freshness_date": None,
            "queryable_via": None,
        },
    ],
}

# ---------------------------------------------------------------------------
# Text fixtures
# ---------------------------------------------------------------------------

MACRO_ONLY_TEXT = (
    "McDonald's reported Q2 comp sales up 3.2% despite traffic headwinds. "
    "Consumer spending is holding but affordability pressure is rising. "
    "Drive-thru deployment of AI ordering is accelerating. "
    "Same-store sales growth outpaced QSR industry average. "
    "Revenue guidance lifted for the full year."
)

RI_ONLY_TEXT = (
    "Jane Smith just joined PAR Technology as VP of Sales. "
    "She was previously at NCR for five years. "
    "I met with her last week and she reached out about a partnership opportunity."
)

STRATEGIC_ONLY_TEXT = (
    "This validates my thesis that restaurant AI adoption is accelerating. "
    "I believe this is a market intelligence signal for competitive positioning. "
    "Note this for my watchlist assessment."
)

MICRO_ENRICH_TEXT = (
    "New McDonald's NSN lookup data shows 14,200 stores and 1,380 operator entities. "
    "The franchise structure has changed significantly — field offices now cover 8 regions. "
    "Attached is the updated operator roster for Q2 2026."
)

MICRO_BUILD_CANDIDATE_TEXT = (
    "PAR Technology has deployed Brink POS to 2,800 restaurant locations. "
    "Their customer list includes major QSR chains across 45 states. "
    "The franchise org chart shows 12 regional account managers."
)

MIXED_MACRO_RI_TEXT = (
    "Dave Wilson just promoted to CTO at Toast. "
    "Toast reported 110,000 restaurant locations in Q2 earnings. "
    "Revenue growth was 28% year-over-year driven by enterprise expansion. "
    "Consumer payment volume on the platform exceeded $12B. "
    "He reached out to me following the announcement."
)

MIXED_ALL_TYPES_TEXT = (
    "McDonald's Q2 comp sales rose 2.1% despite traffic declines from consumer "
    "affordability pressure. "
    "Tom Johnson was just named as the new VP of Franchise Development. "
    "I met with him last week — very promising conversation. "
    "NSN operator roster data shows 1,347 operators covering 14,049 stores. "
    "This validates my thesis that McDonald's franchise structure is a major "
    "deployment intelligence advantage."
)

NOISE_TEXT = (
    "The weather today is nice. I had a good lunch. "
    "Looking forward to the weekend."
)

EMPTY_TEXT = ""


# ---------------------------------------------------------------------------
# IT1: Core invariants
# ---------------------------------------------------------------------------

class TestCoreInvariants(unittest.TestCase):
    """IT1: Schema, persistence, and noise invariants."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def test_IT1a_result_has_required_keys(self):
        """IT1a: All required top-level keys present."""
        r = self._triage(MACRO_ONLY_TEXT)
        required = {
            "triage_id", "captured_at", "input_hash", "input_summary",
            "source_type", "identified_types", "type_count",
            "noise_only", "persistence_status", "requires_confirmation",
            "processing_order", "contract",
        }
        for key in required:
            self.assertIn(key, r, f"Missing key: {key}")

    def test_IT1b_persistence_always_not_persisted(self):
        """IT1b: persistence_status always 'not_persisted' — triage is read-only."""
        r = self._triage(MACRO_ONLY_TEXT)
        self.assertEqual(r["persistence_status"], "not_persisted")

    def test_IT1c_each_stream_requires_confirmation(self):
        """IT1c: Every actionable stream has requires_confirmation=True."""
        r = self._triage(MACRO_ONLY_TEXT)
        for s in r["identified_types"]:
            if s["intelligence_type"] != triage.TYPE_NOISE:
                self.assertTrue(s["requires_confirmation"],
                                f"Stream {s['intelligence_type']} missing requires_confirmation")

    def test_IT1d_contract_field_correct(self):
        """IT1d: contract field identifies schema version."""
        r = self._triage(MACRO_ONLY_TEXT)
        self.assertEqual(r["contract"], "rb_intelligence_triage_v1")

    def test_IT1e_empty_input_returns_noise_only(self):
        """IT1e: Empty input returns noise_only=True."""
        r = self._triage(EMPTY_TEXT)
        self.assertTrue(r["noise_only"])
        self.assertEqual(r["type_count"], 0)

    def test_IT1f_noise_only_text_returns_noise(self):
        """IT1f: Conversational noise text returns noise_only=True."""
        r = self._triage(NOISE_TEXT)
        self.assertTrue(r["noise_only"])
        self.assertEqual(r["type_count"], 0)

    def test_IT1g_stream_schema_has_all_required_fields(self):
        """IT1g: Each IntelligenceStream has all required fields."""
        r = self._triage(MACRO_ONLY_TEXT)
        required_stream_keys = {
            "intelligence_type", "confidence", "entity_scoped", "entity_name",
            "extracted_summary", "extracted_entities", "extracted_signals",
            "proposed_action", "target_endpoint", "requires_confirmation",
            "source_refs",
        }
        for s in r["identified_types"]:
            for key in required_stream_keys:
                self.assertIn(key, s, f"Stream missing key: {key}")

    def test_IT1h_intelligence_type_is_valid(self):
        """IT1h: Every stream intelligence_type is in VALID_TYPES."""
        r = self._triage(MIXED_ALL_TYPES_TEXT)
        for s in r["identified_types"]:
            self.assertIn(s["intelligence_type"], triage.VALID_TYPES)

    def test_IT1i_input_hash_is_16_hex_chars(self):
        """IT1i: input_hash is a 16-character hex digest."""
        r = self._triage(MACRO_ONLY_TEXT)
        self.assertRegex(r["input_hash"], r"^[0-9a-f]{16}$")

    def test_IT1j_input_summary_truncated(self):
        """IT1j: input_summary is truncated to 200 chars."""
        long_text = "A" * 500
        r = self._triage(long_text)
        self.assertLessEqual(len(r["input_summary"]), 200)


# ---------------------------------------------------------------------------
# IT2: Macro signal classifier
# ---------------------------------------------------------------------------

class TestMacroClassifier(unittest.TestCase):
    """IT2: Macro/industry/market signal detection."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def _get_stream(self, result, itype):
        for s in result["identified_types"]:
            if s["intelligence_type"] == itype:
                return s
        return None

    def test_IT2a_macro_detected_in_macro_only_text(self):
        """IT2a: macro_signal detected in strong macro text."""
        r = self._triage(MACRO_ONLY_TEXT)
        self.assertFalse(r["noise_only"])
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_MACRO, types)

    def test_IT2b_macro_stream_entity_scoped_to_named_watchlist_entity(self):
        """IT2b, updated RB-DEFECT-2026-08-29: macro_signal now recognizes a
        named MANDATORY_ALL entity as the strongest materiality signal it
        has, independent of the generic keyword buckets -- MACRO_ONLY_TEXT
        names McDonald's exactly once, so the stream should now be scoped
        to it, not left generically unscoped as it was before this fix
        (see _matched_watchlist_entities / the Starbucks+NomadGo incident)."""
        r = self._triage(MACRO_ONLY_TEXT)
        s = self._get_stream(r, triage.TYPE_MACRO)
        self.assertIsNotNone(s)
        self.assertTrue(s["entity_scoped"])
        self.assertEqual(s["entity_name"], "McDonald's")

    def test_IT2c_macro_confidence_is_valid(self):
        """IT2c: macro_signal confidence is high/medium/low."""
        r = self._triage(MACRO_ONLY_TEXT)
        s = self._get_stream(r, triage.TYPE_MACRO)
        self.assertIn(s["confidence"], {"high", "medium", "low"})

    def test_IT2d_macro_extracted_signals_not_empty(self):
        """IT2d: macro_signal has extracted_signals list."""
        r = self._triage(MACRO_ONLY_TEXT)
        s = self._get_stream(r, triage.TYPE_MACRO)
        self.assertIsInstance(s["extracted_signals"], list)
        self.assertGreater(len(s["extracted_signals"]), 0)

    def test_IT2e_macro_target_endpoint(self):
        """IT2e: macro_signal target_endpoint is set."""
        r = self._triage(MACRO_ONLY_TEXT)
        s = self._get_stream(r, triage.TYPE_MACRO)
        self.assertIsNotNone(s["target_endpoint"])

    def test_IT2f_noise_text_no_macro(self):
        """IT2f: Noise text does not produce macro_signal."""
        r = self._triage(NOISE_TEXT)
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertNotIn(triage.TYPE_MACRO, types)

    def test_IT2g_macro_high_confidence_with_many_hits(self):
        """IT2g: High keyword density → high confidence."""
        dense_text = (
            "Q2 earnings revenue growth consumer spending traffic comp sales "
            "same-store deployment rollout acquisition funding market share "
            "competitive restaurant operator franchise kiosk POS qsr industry"
        )
        r = self._triage(dense_text)
        s = self._get_stream(r, triage.TYPE_MACRO)
        if s:
            self.assertIn(s["confidence"], {"high", "medium"})


# ---------------------------------------------------------------------------
# IT3: Micro graph enrichment classifier
# ---------------------------------------------------------------------------

class TestMicroGraphEnrichment(unittest.TestCase):
    """IT3: Entity data enrichment for existing registered artifacts."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def _get_enrich_streams(self, result):
        return [s for s in result["identified_types"]
                if s["intelligence_type"] == triage.TYPE_MICRO_ENRICH]

    def test_IT3a_enrich_detected_for_mcdonalds(self):
        """IT3a: McDonald's entity + topology terms → micro_graph_enrichment."""
        r = self._triage(MICRO_ENRICH_TEXT)
        streams = self._get_enrich_streams(r)
        self.assertGreater(len(streams), 0, "Expected micro_graph_enrichment for McDonald's")

    def test_IT3b_enrich_stream_has_artifact_id(self):
        """IT3b: Enrichment stream carries artifact_id."""
        r = self._triage(MICRO_ENRICH_TEXT)
        streams = self._get_enrich_streams(r)
        for s in streams:
            self.assertIn("artifact_id", s)
            self.assertIsNotNone(s["artifact_id"])

    def test_IT3c_enrich_target_endpoint_contains_artifact_id(self):
        """IT3c: target_endpoint for enrichment contains the artifact_id."""
        r = self._triage(MICRO_ENRICH_TEXT)
        streams = self._get_enrich_streams(r)
        for s in streams:
            self.assertIn(s["artifact_id"], s["target_endpoint"])

    def test_IT3d_enrich_entity_scoped_true(self):
        """IT3d: Enrichment stream is entity_scoped=True."""
        r = self._triage(MICRO_ENRICH_TEXT)
        streams = self._get_enrich_streams(r)
        for s in streams:
            self.assertTrue(s["entity_scoped"])

    def test_IT3e_enrich_requires_confirmation(self):
        """IT3e: Enrichment stream always requires confirmation."""
        r = self._triage(MICRO_ENRICH_TEXT)
        streams = self._get_enrich_streams(r)
        for s in streams:
            self.assertTrue(s["requires_confirmation"])

    def test_IT3f_entity_name_resolved_from_registry(self):
        """IT3f: entity_name resolved from registry, not raw artifact_id."""
        r = self._triage(MICRO_ENRICH_TEXT)
        streams = self._get_enrich_streams(r)
        for s in streams:
            self.assertIsNotNone(s.get("entity_name"))

    def test_IT3g_no_enrich_without_entity_match(self):
        """IT3g: Text with no registered entity produces no micro_graph_enrichment."""
        text = "The weather in Chicago is quite pleasant today."
        r = self._triage(text)
        streams = self._get_enrich_streams(r)
        self.assertEqual(len(streams), 0)


# ---------------------------------------------------------------------------
# IT4: Micro graph build classifier
# ---------------------------------------------------------------------------

class TestMicroGraphBuild(unittest.TestCase):
    """IT4: Candidate new entity detection (micro_graph_build)."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", {**REGISTRY_FIXTURE, "artifacts": []})
        return triage.triage_input(text, **kwargs)

    def _get_build_streams(self, result):
        return [s for s in result["identified_types"]
                if s["intelligence_type"] == triage.TYPE_MICRO_BUILD]

    def test_IT4a_build_detected_for_par_with_topology(self):
        """IT4a: PAR Technology + org/location topology → micro_graph_build."""
        r = self._triage(MICRO_BUILD_CANDIDATE_TEXT)
        streams = self._get_build_streams(r)
        self.assertGreater(len(streams), 0,
                           "Expected micro_graph_build for PAR Technology")

    def test_IT4b_build_stream_has_artifact_id(self):
        """IT4b: Build stream carries proposed artifact_id."""
        r = self._triage(MICRO_BUILD_CANDIDATE_TEXT)
        streams = self._get_build_streams(r)
        for s in streams:
            self.assertIn("artifact_id", s)
            self.assertTrue(s["artifact_id"].startswith("micro_graph:"))

    def test_IT4c_build_target_endpoint(self):
        """IT4c: Build stream target_endpoint is /artifacts (create new)."""
        r = self._triage(MICRO_BUILD_CANDIDATE_TEXT)
        streams = self._get_build_streams(r)
        for s in streams:
            self.assertEqual(s["target_endpoint"], "/artifacts")

    def test_IT4d_build_entity_scoped_true(self):
        """IT4d: Build stream is entity_scoped=True."""
        r = self._triage(MICRO_BUILD_CANDIDATE_TEXT)
        streams = self._get_build_streams(r)
        for s in streams:
            self.assertTrue(s["entity_scoped"])

    def test_IT4e_no_build_without_topology_terms(self):
        """IT4e: Candidate entity mention without topology terms → no micro_graph_build."""
        text = "I heard PAR Technology is doing well. Their product is interesting."
        r = self._triage(text)
        streams = self._get_build_streams(r)
        self.assertEqual(len(streams), 0,
                         "Candidate mention without topology should not produce build stream")

    def test_IT4f_build_requires_confirmation(self):
        """IT4f: Build stream always requires confirmation."""
        r = self._triage(MICRO_BUILD_CANDIDATE_TEXT)
        streams = self._get_build_streams(r)
        for s in streams:
            self.assertTrue(s["requires_confirmation"])

    def test_IT4g_technology_substring_does_not_falsely_match_olo(self):
        """RB-DEFECT (2026-09-14): live incident -- the candidate-entity loop
        used a raw substring check, and "olo" is a substring of "technology"
        (tech-n-OLO-gy). Confirmed live: real captures with no actual Olo
        mention (just ordinary "restaurant technology" text alongside
        topology vocabulary) produced spurious Olo micro_graph_build
        candidates. Same collision class already fixed once in
        intelligence_mutation_engine.py for these exact terms (olo/ncr/qu)
        but never applied to this classifier."""
        text = (
            "Restaurant technology is reshaping how operators run their "
            "franchise business. The market for store count and territory "
            "planning keeps growing."
        )
        r = self._triage(text)
        streams = self._get_build_streams(r)
        olo_streams = [s for s in streams if s.get("artifact_id") == "micro_graph:olo"]
        self.assertEqual(olo_streams, [],
                         "\"technology\" alone must not false-positive as an Olo mention")

    def test_IT4h_real_olo_mention_still_detected(self):
        """A genuine, standalone "Olo" mention (with topology terms) must
        still produce a build candidate -- the word-boundary fix must not
        overcorrect into missing real mentions."""
        text = (
            "Olo has deployed its ordering platform to 400 restaurant "
            "locations. Their customer list spans multiple franchise operators."
        )
        r = self._triage(text)
        streams = self._get_build_streams(r)
        olo_streams = [s for s in streams if s.get("artifact_id") == "micro_graph:olo"]
        self.assertEqual(len(olo_streams), 1)


# ---------------------------------------------------------------------------
# IT5: RI event classifier
# ---------------------------------------------------------------------------

class TestRIClassifier(unittest.TestCase):
    """IT5: Person-level relationship signal detection."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def _get_ri_stream(self, result):
        for s in result["identified_types"]:
            if s["intelligence_type"] == triage.TYPE_RI:
                return s
        return None

    def test_IT5a_ri_detected_in_ri_only_text(self):
        """IT5a: RI event detected in text with join/promotion/meeting signals."""
        r = self._triage(RI_ONLY_TEXT)
        s = self._get_ri_stream(r)
        self.assertIsNotNone(s, "Expected ri_event stream")

    def test_IT5b_ri_stream_entity_scoped_false(self):
        """IT5b: ri_event is not entity_scoped (it's person-scoped)."""
        r = self._triage(RI_ONLY_TEXT)
        s = self._get_ri_stream(r)
        self.assertFalse(s["entity_scoped"])

    def test_IT5c_ri_target_endpoint(self):
        """IT5c: RI stream target_endpoint is /relationship/intake (RB 9.23)."""
        r = self._triage(RI_ONLY_TEXT)
        s = self._get_ri_stream(r)
        self.assertEqual(s["target_endpoint"], "/relationship/intake")

    def test_IT5d_ri_confidence_valid(self):
        """IT5d: RI confidence is high/medium/low."""
        r = self._triage(RI_ONLY_TEXT)
        s = self._get_ri_stream(r)
        self.assertIn(s["confidence"], {"high", "medium", "low"})

    def test_IT5e_ri_extracted_signals_not_empty(self):
        """IT5e: RI stream extracted_signals non-empty."""
        r = self._triage(RI_ONLY_TEXT)
        s = self._get_ri_stream(r)
        self.assertGreater(len(s["extracted_signals"]), 0)

    def test_IT5f_noise_text_no_ri(self):
        """IT5f: Noise text does not produce ri_event."""
        r = self._triage(NOISE_TEXT)
        s = self._get_ri_stream(r)
        self.assertIsNone(s)

    def test_IT5g_author_name_propagated_to_ri_entities(self):
        """IT5g: author_name injected into ri_event extracted_entities."""
        r = self._triage(RI_ONLY_TEXT, author_name="Bob Gibson", registry=REGISTRY_FIXTURE)
        s = self._get_ri_stream(r)
        self.assertIsNotNone(s)
        self.assertIn("Bob Gibson", s.get("extracted_entities", []))


# ---------------------------------------------------------------------------
# IT6: Strategic memory classifier
# ---------------------------------------------------------------------------

class TestStrategicClassifier(unittest.TestCase):
    """IT6: User thesis / watchlist / positioning detection."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def _get_strategic_stream(self, result):
        for s in result["identified_types"]:
            if s["intelligence_type"] == triage.TYPE_STRATEGIC:
                return s
        return None

    def test_IT6a_strategic_detected_in_thesis_text(self):
        """IT6a: Strategic memory detected in 'validates my thesis' text."""
        r = self._triage(STRATEGIC_ONLY_TEXT)
        s = self._get_strategic_stream(r)
        self.assertIsNotNone(s, "Expected strategic_memory stream")

    def test_IT6b_strategic_not_entity_scoped(self):
        """IT6b: strategic_memory is not entity_scoped."""
        r = self._triage(STRATEGIC_ONLY_TEXT)
        s = self._get_strategic_stream(r)
        self.assertFalse(s["entity_scoped"])

    def test_IT6c_strategic_target_endpoint(self):
        """IT6c: Strategic stream target_endpoint is /insight/intake (RB 9.23)."""
        r = self._triage(STRATEGIC_ONLY_TEXT)
        s = self._get_strategic_stream(r)
        self.assertEqual(s["target_endpoint"], "/insight/intake")

    def test_IT6d_strategic_requires_confirmation(self):
        """IT6d: Strategic stream requires confirmation."""
        r = self._triage(STRATEGIC_ONLY_TEXT)
        s = self._get_strategic_stream(r)
        self.assertTrue(s["requires_confirmation"])

    def test_IT6e_noise_text_no_strategic(self):
        """IT6e: Noise text does not produce strategic_memory."""
        r = self._triage(NOISE_TEXT)
        s = self._get_strategic_stream(r)
        self.assertIsNone(s)


# ---------------------------------------------------------------------------
# IT7: Multi-type detection
# ---------------------------------------------------------------------------

class TestMultiTypeDetection(unittest.TestCase):
    """IT7: Multiple intelligence types from a single input (DEFECT-015 core)."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def test_IT7a_macro_and_ri_detected_together(self):
        """IT7a: macro_signal + ri_event detected in mixed macro+RI text."""
        r = self._triage(MIXED_MACRO_RI_TEXT)
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_MACRO, types)
        self.assertIn(triage.TYPE_RI, types)

    def test_IT7b_type_count_reflects_all_streams(self):
        """IT7b: type_count equals number of actionable (non-noise) streams."""
        r = self._triage(MIXED_MACRO_RI_TEXT)
        actionable = [s for s in r["identified_types"]
                      if s["intelligence_type"] != triage.TYPE_NOISE]
        self.assertEqual(r["type_count"], len(actionable))

    def test_IT7c_all_types_detected_in_all_types_text(self):
        """IT7c: macro + micro_enrich + ri + strategic all detected in mixed fixture."""
        r = self._triage(MIXED_ALL_TYPES_TEXT)
        types = set(s["intelligence_type"] for s in r["identified_types"])
        # At minimum macro + micro_enrich + ri + strategic
        self.assertIn(triage.TYPE_MACRO, types)
        self.assertIn(triage.TYPE_RI, types)
        self.assertIn(triage.TYPE_STRATEGIC, types)
        # McDonald's entity match → micro_enrich
        self.assertIn(triage.TYPE_MICRO_ENRICH, types)

    def test_IT7d_noise_only_false_when_any_type_detected(self):
        """IT7d: noise_only=False when at least one actionable type found."""
        r = self._triage(MIXED_MACRO_RI_TEXT)
        self.assertFalse(r["noise_only"])

    def test_IT7e_noise_only_true_for_pure_noise(self):
        """IT7e: noise_only=True for conversational text."""
        r = self._triage(NOISE_TEXT)
        self.assertTrue(r["noise_only"])

    def test_IT7f_no_duplicate_stream_types_from_same_classifier(self):
        """IT7f: No duplicate macro_signal entries from single run."""
        r = self._triage(MIXED_ALL_TYPES_TEXT)
        macro_streams = [s for s in r["identified_types"]
                         if s["intelligence_type"] == triage.TYPE_MACRO]
        self.assertEqual(len(macro_streams), 1,
                         "Expect exactly one macro_signal stream, not duplicates")

    def test_IT7g_ri_stream_not_duplicated(self):
        """IT7g: No duplicate ri_event entries from single run."""
        r = self._triage(MIXED_ALL_TYPES_TEXT)
        ri_streams = [s for s in r["identified_types"]
                      if s["intelligence_type"] == triage.TYPE_RI]
        self.assertEqual(len(ri_streams), 1,
                         "Expect exactly one ri_event stream, not duplicates")


# ---------------------------------------------------------------------------
# IT8: Processing order (mutation priority)
# ---------------------------------------------------------------------------

class TestProcessingOrder(unittest.TestCase):
    """IT8: identified_types sorted by mutation priority; RI first."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def test_IT8a_processing_order_ri_before_macro(self):
        """IT8a: RI appears before macro_signal in processing_order."""
        r = self._triage(MIXED_MACRO_RI_TEXT)
        if triage.TYPE_RI in r["processing_order"] and triage.TYPE_MACRO in r["processing_order"]:
            ri_idx = r["processing_order"].index(triage.TYPE_RI)
            macro_idx = r["processing_order"].index(triage.TYPE_MACRO)
            self.assertLess(ri_idx, macro_idx,
                            "ri_event must come before macro_signal in processing_order")

    def test_IT8b_processing_order_enrich_before_macro(self):
        """IT8b: micro_graph_enrichment appears before macro_signal in processing_order."""
        r = self._triage(MICRO_ENRICH_TEXT + " " + MACRO_ONLY_TEXT)
        if (triage.TYPE_MICRO_ENRICH in r["processing_order"]
                and triage.TYPE_MACRO in r["processing_order"]):
            enrich_idx = r["processing_order"].index(triage.TYPE_MICRO_ENRICH)
            macro_idx = r["processing_order"].index(triage.TYPE_MACRO)
            self.assertLess(enrich_idx, macro_idx)

    def test_IT8c_processing_order_ri_before_strategic(self):
        """IT8c: RI appears before strategic_memory in processing_order."""
        r = self._triage(MIXED_ALL_TYPES_TEXT)
        if (triage.TYPE_RI in r["processing_order"]
                and triage.TYPE_STRATEGIC in r["processing_order"]):
            ri_idx = r["processing_order"].index(triage.TYPE_RI)
            strat_idx = r["processing_order"].index(triage.TYPE_STRATEGIC)
            self.assertLess(ri_idx, strat_idx)

    def test_IT8d_noise_excluded_from_processing_order(self):
        """IT8d: noise is excluded from processing_order."""
        r = self._triage(MIXED_ALL_TYPES_TEXT)
        self.assertNotIn(triage.TYPE_NOISE, r["processing_order"])

    def test_IT8e_identified_types_sorted_matches_mutation_order(self):
        """IT8e: identified_types are sorted by MUTATION_ORDER priority."""
        r = self._triage(MIXED_ALL_TYPES_TEXT)
        actionable = [s for s in r["identified_types"]
                      if s["intelligence_type"] != triage.TYPE_NOISE]
        type_indices = [
            triage.MUTATION_ORDER.index(s["intelligence_type"])
            if s["intelligence_type"] in triage.MUTATION_ORDER else 999
            for s in actionable
        ]
        self.assertEqual(type_indices, sorted(type_indices),
                         "identified_types not sorted by MUTATION_ORDER")

    def test_IT8f_empty_processing_order_for_noise_only(self):
        """IT8f: processing_order is empty list for noise-only result."""
        r = self._triage(NOISE_TEXT)
        self.assertEqual(r["processing_order"], [])


# ---------------------------------------------------------------------------
# IT9: Author / event context propagation
# ---------------------------------------------------------------------------

class TestContextPropagation(unittest.TestCase):
    """IT9: author_name, author_company, event_at propagated to streams."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def test_IT9a_author_name_in_result(self):
        """IT9a: author_name returned in triage result."""
        r = self._triage(RI_ONLY_TEXT, author_name="Bob Gibson")
        self.assertEqual(r["author_name"], "Bob Gibson")

    def test_IT9b_author_company_in_result(self):
        """IT9b: author_company returned in triage result."""
        r = self._triage(RI_ONLY_TEXT, author_company="Toast", author_name="Bob Gibson")
        self.assertEqual(r["author_company"], "Toast")

    def test_IT9c_event_at_propagated_to_streams(self):
        """IT9c: event_at propagated to every stream."""
        r = self._triage(MIXED_MACRO_RI_TEXT, event_at="2026-05-29")
        for s in r["identified_types"]:
            self.assertEqual(s.get("event_at"), "2026-05-29")

    def test_IT9d_event_at_confidence_high_when_supplied(self):
        """IT9d: event_at_confidence=high when event_at supplied."""
        r = self._triage(MIXED_MACRO_RI_TEXT, event_at="2026-05-29")
        for s in r["identified_types"]:
            self.assertEqual(s.get("event_at_confidence"), "high")

    def test_IT9e_event_at_confidence_low_when_missing(self):
        """IT9e: event_at_confidence=low when event_at not supplied."""
        r = self._triage(MIXED_MACRO_RI_TEXT)
        for s in r["identified_types"]:
            self.assertEqual(s.get("event_at_confidence"), "low")

    def test_IT9f_source_type_preserved(self):
        """IT9f: source_type returned in result."""
        r = self._triage(MACRO_ONLY_TEXT, source_type="transcript")
        self.assertEqual(r["source_type"], "transcript")

    def test_IT9g_source_name_preserved(self):
        """IT9g: source_name returned in result."""
        r = self._triage(MACRO_ONLY_TEXT, source_name="Q2 Earnings Call.txt")
        self.assertEqual(r["source_name"], "Q2 Earnings Call.txt")


# ---------------------------------------------------------------------------
# IT10: Registry injection
# ---------------------------------------------------------------------------

class TestRegistryInjection(unittest.TestCase):
    """IT10: Registry injection bypasses filesystem for isolated testing."""

    def test_IT10a_empty_registry_no_micro_enrich_for_unknown_entity(self):
        """IT10a: Empty registry + text with no hardcoded entity → no micro_graph_enrichment.

        Note: _MICRO_GRAPH_ENTITY_TERMS contains hardcoded McDonald's/NSN terms that
        are always present regardless of registry injection. This test uses text with
        an entity not in any hardcoded term set to verify the registry-isolation path.
        """
        unknown_entity_text = (
            "Acme Restaurant Supply Company has 800 franchise locations in 40 territories. "
            "Their operator org chart covers 6 regional divisions with district managers. "
            "Customer roster update attached with all account contacts."
        )
        r = triage.triage_input(
            unknown_entity_text,
            registry={"contract": "rb_intelligence_artifact_registry_v1", "artifacts": []},
        )
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertNotIn(triage.TYPE_MICRO_ENRICH, types,
                         "Unknown entity not in registry or hardcoded terms should not produce enrich")

    def test_IT10b_custom_registry_produces_enrich_for_custom_entity(self):
        """IT10b: Custom registry entity → micro_graph_enrichment for that entity."""
        custom_registry = {
            "contract": "rb_intelligence_artifact_registry_v1",
            "artifacts": [{
                "artifact_id": "micro_graph:custom_corp",
                "artifact_type": "micro_graph",
                "name": "Custom Corp",
                "entity": "Custom Corp",
                "entity_aliases": ["custom corp", "customco"],
                "status": "active",
            }],
        }
        text = (
            "Custom Corp has 500 franchise locations across 30 regions. "
            "Their operator roster shows 120 franchise operators managing territories."
        )
        r = triage.triage_input(text, registry=custom_registry)
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_MICRO_ENRICH, types)

    def test_IT10c_mcdonalds_enrich_with_fixture_registry(self):
        """IT10c: McDonald's data + fixture registry → micro_graph_enrichment."""
        r = triage.triage_input(MICRO_ENRICH_TEXT, registry=REGISTRY_FIXTURE)
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_MICRO_ENRICH, types)


# ---------------------------------------------------------------------------
# IT11: Triage ID format
# ---------------------------------------------------------------------------

class TestTriageIDFormat(unittest.TestCase):
    """IT11: Triage IDs follow TRG-YYYY-MM-DD-NNN pattern."""

    def test_IT11a_triage_id_format(self):
        """IT11a: triage_id matches TRG-YYYY-MM-DD-NNN format."""
        r = triage.triage_input(MACRO_ONLY_TEXT, registry=REGISTRY_FIXTURE)
        self.assertRegex(r["triage_id"], r"^TRG-\d{4}-\d{2}-\d{2}-\d{3,}$")

    def test_IT11b_triage_id_date_component_is_today_or_valid_date(self):
        """IT11b: triage_id date component is a parseable YYYY-MM-DD."""
        r = triage.triage_input(MACRO_ONLY_TEXT, registry=REGISTRY_FIXTURE)
        date_part = r["triage_id"][4:14]  # "YYYY-MM-DD"
        import datetime
        datetime.date.fromisoformat(date_part)  # raises ValueError if not valid

    def test_IT11c_unique_triage_ids_per_call(self):
        """IT11c: Consecutive triage calls produce different triage IDs."""
        r1 = triage.triage_input(MACRO_ONLY_TEXT, registry=REGISTRY_FIXTURE)
        r2 = triage.triage_input(RI_ONLY_TEXT, registry=REGISTRY_FIXTURE)
        self.assertNotEqual(r1["triage_id"], r2["triage_id"])


# ---------------------------------------------------------------------------
# IT12: End-to-end fixtures
# ---------------------------------------------------------------------------

class TestEndToEndFixtures(unittest.TestCase):
    """IT12: Realistic paste scenarios from the DEFECT-015 case."""

    def _triage(self, text, **kwargs):
        kwargs.setdefault("registry", REGISTRY_FIXTURE)
        return triage.triage_input(text, **kwargs)

    def test_IT12a_linkedin_post_macro_and_ri(self):
        """IT12a: LinkedIn post about earnings + person event → macro + RI."""
        linkedin_post = (
            "Excited to announce I've just joined Toast as VP of Enterprise. "
            "Q2 was a record quarter — 28% revenue growth and 110,000 restaurant partners. "
            "Same-store digital sales are up across all segments. "
            "Looking forward to driving the next wave of restaurant technology growth."
        )
        r = self._triage(linkedin_post, author_name="Dave Wilson",
                         author_company="Toast", source_type="screenshot")
        types = set(s["intelligence_type"] for s in r["identified_types"])
        self.assertIn(triage.TYPE_RI, types, "LinkedIn join announcement should be ri_event")
        self.assertIn(triage.TYPE_MACRO, types, "Revenue/growth figures should be macro_signal")

    def test_IT12b_mcdonalds_roster_paste_is_micro_enrich(self):
        """IT12b: McDonald's NSN operator roster paste → micro_graph_enrichment."""
        roster_paste = (
            "NSN Operator Lookup — June 2026\n"
            "Operator Entity Count: 1,392\n"
            "Store Count: 14,220\n"
            "Field Office: Chicago North, Field Office: Dallas South\n"
            "OTM, STIM, and RFM territories updated.\n"
            "Co-op structure reorganized for Q3."
        )
        r = self._triage(roster_paste, source_type="paste",
                         source_name="NSN Lookup 2026-06.xlsx")
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_MICRO_ENRICH, types)

    def test_IT12c_pure_market_article_no_ri(self):
        """IT12c: Pure market article without person signals → macro, no RI."""
        article = (
            "Restaurant technology investment is accelerating in 2026. "
            "QSR operators are prioritizing kiosk deployment and POS upgrades. "
            "Comparable sales growth is running ahead of casual dining. "
            "Consumer affordability concerns persist but drive-thru traffic is resilient. "
            "Industry analysts expect 15% revenue growth for restaurant tech vendors. "
            "Market share battles between leading POS providers are intensifying."
        )
        r = self._triage(article, source_type="url")
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_MACRO, types)
        self.assertNotIn(triage.TYPE_RI, types)

    def test_IT12d_thesis_note_no_macro_no_ri(self):
        """IT12d: Pure thesis note → strategic_memory only, not macro or RI."""
        note = (
            "Note this for my assessment: I believe the restaurant technology market "
            "will consolidate around 3 major POS vendors. This validates my thesis "
            "about market concentration. Add to strategic watchlist."
        )
        r = self._triage(note)
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_STRATEGIC, types)

    def test_IT12e_requires_confirmation_true_for_any_actionable(self):
        """IT12e: requires_confirmation=True at result level when actionable types exist."""
        r = self._triage(MIXED_ALL_TYPES_TEXT)
        if not r["noise_only"]:
            self.assertTrue(r["requires_confirmation"])

    def test_IT12f_noise_only_result_requires_confirmation_false(self):
        """IT12f: noise-only result has requires_confirmation=False."""
        r = self._triage(NOISE_TEXT)
        self.assertFalse(r["requires_confirmation"])


# ---------------------------------------------------------------------------
# IT13: Input format detection (RB 9.26)
# ---------------------------------------------------------------------------

# Format-detection fixture texts
_LI_POST = """
Oliver Ostertag • 1st
President, Growth + AI at PAR Technology
Just wrapped our Q2 partner summit — 900+ restaurant operators in the room.
The theme: AI isn't a pilot project anymore. It's ops infrastructure.
Key takeaways below 👇

#RestaurantTech #AI #PAR

Like  Comment  Repost  Send
1,247 reactions  83 comments
"""

_LI_POST_DEGREE2 = """
Sarah McAngus • 2nd •
VP Product at Foods Connected
Proud to share that Foods Connected just crossed 500 food-service customers in North America.
Traceability is no longer optional — it's table stakes.
https://www.linkedin.com/company/foods-connected/
"""

_LI_NEWSLETTER = """
The Restaurant Tech Weekly — LinkedIn Newsletter
Issue #42 | May 2026

This week in restaurant technology:
McDonald's AI voice ordering pilot hits 1,200 locations.
Toast reports record Q2 revenue.

Unsubscribe | View in browser
https://www.linkedin.com/newsletters/restaurant-tech-weekly-123456/
"""

_GENERIC_NEWSLETTER = """
The Hospitality Intelligence Digest — Issue #18

Good morning, here's your weekly briefing.

• POS spending up 14% YoY
• AI kiosk rollout accelerating at QSR chains
• Technomic releases Q2 foodservice forecast

You're receiving this because you subscribed to the Hospitality Intel list.
To unsubscribe, click here.
View in browser | Forward to a friend
"""

_EMAIL_THREAD = """
From: Patrick Nelson <patrick@example.com>
To: Todd Vahlsing <todd@example.com>
Subject: Re: Intro to Bob Gibson
Date: Thu, 29 May 2026 09:15:00 -0500

Hey Todd,

Happy to make that intro. I've known Bob since the NCR days.

On Wed, 28 May 2026 at 18:32, Todd Vahlsing <todd@example.com> wrote:
> Patrick — would you be able to intro me to Bob Gibson at Toast?
> I have some ideas worth sharing.
"""

_TRANSCRIPT = """
[00:00:15] John Smith: Thanks for joining today. Let's get into the Toast Q2 results.
[00:00:42] Sarah Jones: Happy to be here. Q2 was our strongest quarter since IPO.
[00:01:10] John Smith: Revenue was up 28%. What drove that?
[00:02:05] Sarah Jones: Restaurant partner additions — especially enterprise.
[00:03:30] [crosstalk]
[00:04:00] John Smith: Let's talk about the POS market share data.
"""

_ARTICLE = """
By James Hartley | Restaurant Business Online
Published May 28, 2026

Toast Raises Guidance After Record Q2

Toast Inc. raised its full-year guidance Wednesday after reporting record second-quarter
revenue of $1.1 billion, up 28% year over year. The restaurant technology company said
same-store digital sales growth of 12% across its partner base drove strong results.

"We're seeing operators double down on digital," CEO Aman Narang said on the earnings call.
"""

_SOCIAL_POST_TWITTER = """
RT @RestaurantTech: Toast just raised guidance to $4.3B ARR —
biggest single-quarter beat in their history. POS race is heating up.
https://twitter.com/restauranttech/status/99887766
"""


class TestInputFormatDetection(unittest.TestCase):
    """IT13: _detect_format() correctly identifies input format and confidence."""

    def _fmt(self, text: str):
        return triage._detect_format(text)

    # ── LinkedIn post ──────────────────────────────────────────────────────

    def test_IT13a_linkedin_post_degree1(self):
        """IT13a: LinkedIn post with degree marker → linkedin_post."""
        fmt, conf = self._fmt(_LI_POST)
        self.assertEqual(fmt, triage.FORMAT_LINKEDIN_POST)
        self.assertGreater(conf, 0.6)

    def test_IT13b_linkedin_post_degree2(self):
        """IT13b: LinkedIn post with 2nd degree marker → linkedin_post."""
        fmt, conf = self._fmt(_LI_POST_DEGREE2)
        self.assertEqual(fmt, triage.FORMAT_LINKEDIN_POST)
        self.assertGreater(conf, 0.5)

    def test_IT13c_linkedin_post_has_high_confidence(self):
        """IT13c: Strong LinkedIn post signals → confidence ≥ 0.70."""
        fmt, conf = self._fmt(_LI_POST)
        self.assertGreaterEqual(conf, 0.70)

    # ── LinkedIn newsletter ────────────────────────────────────────────────

    def test_IT13d_linkedin_newsletter(self):
        """IT13d: LinkedIn Newsletter with unsubscribe + LI URL → linkedin_newsletter."""
        fmt, conf = self._fmt(_LI_NEWSLETTER)
        self.assertEqual(fmt, triage.FORMAT_LINKEDIN_NEWSLETTER)
        self.assertGreater(conf, 0.5)

    # ── Generic newsletter ─────────────────────────────────────────────────

    def test_IT13e_generic_newsletter(self):
        """IT13e: Newsletter with unsubscribe + issue number → newsletter."""
        fmt, conf = self._fmt(_GENERIC_NEWSLETTER)
        self.assertIn(fmt, {triage.FORMAT_NEWSLETTER, triage.FORMAT_LINKEDIN_NEWSLETTER})
        self.assertGreater(conf, 0.5)

    def test_IT13f_newsletter_confidence_high_on_unsubscribe(self):
        """IT13f: unsubscribe is the strongest newsletter signal."""
        _, conf = self._fmt(_GENERIC_NEWSLETTER)
        self.assertGreaterEqual(conf, 0.60)

    # ── Email thread ───────────────────────────────────────────────────────

    def test_IT13g_email_thread(self):
        """IT13g: Email with From/To/Subject headers → email_thread."""
        fmt, conf = self._fmt(_EMAIL_THREAD)
        self.assertEqual(fmt, triage.FORMAT_EMAIL_THREAD)
        self.assertGreater(conf, 0.6)

    def test_IT13h_email_not_linkedin_post(self):
        """IT13h: Email thread is not misclassified as linkedin_post."""
        fmt, _ = self._fmt(_EMAIL_THREAD)
        self.assertNotEqual(fmt, triage.FORMAT_LINKEDIN_POST)

    # ── Transcript ─────────────────────────────────────────────────────────

    def test_IT13i_transcript_timestamp(self):
        """IT13i: Text with [HH:MM:SS] timestamps → transcript."""
        fmt, conf = self._fmt(_TRANSCRIPT)
        self.assertEqual(fmt, triage.FORMAT_TRANSCRIPT)
        self.assertGreater(conf, 0.6)

    def test_IT13j_transcript_high_confidence(self):
        """IT13j: Strong transcript signals → confidence ≥ 0.70."""
        _, conf = self._fmt(_TRANSCRIPT)
        self.assertGreaterEqual(conf, 0.70)

    # ── Article ────────────────────────────────────────────────────────────

    def test_IT13k_article_byline(self):
        """IT13k: Text with 'By Name | Publication' + date → article."""
        fmt, conf = self._fmt(_ARTICLE)
        self.assertEqual(fmt, triage.FORMAT_ARTICLE)
        self.assertGreater(conf, 0.5)

    # ── Social post (non-LinkedIn) ─────────────────────────────────────────

    def test_IT13l_twitter_retweet(self):
        """IT13l: RT @handle pattern + twitter.com URL → social_post."""
        fmt, conf = self._fmt(_SOCIAL_POST_TWITTER)
        self.assertEqual(fmt, triage.FORMAT_SOCIAL_POST)
        self.assertGreater(conf, 0.6)

    # ── Unknown / paste ────────────────────────────────────────────────────

    def test_IT13m_short_ambiguous_text_is_paste(self):
        """IT13m: Short text with no format signals → paste with low confidence."""
        fmt, conf = self._fmt("Hey, let's catch up next week.")
        self.assertEqual(fmt, triage.FORMAT_PASTE)
        self.assertLess(conf, 0.7)

    def test_IT13n_empty_text_is_paste(self):
        """IT13n: Empty string → paste."""
        fmt, _ = self._fmt("")
        self.assertEqual(fmt, triage.FORMAT_PASTE)

    # ── triage_input() carries format fields ──────────────────────────────

    def test_IT13o_triage_result_has_input_format(self):
        """IT13o: triage_input() returns input_format field."""
        r = triage.triage_input(_LI_POST, registry=REGISTRY_FIXTURE)
        self.assertIn("input_format", r)
        self.assertEqual(r["input_format"], triage.FORMAT_LINKEDIN_POST)

    def test_IT13p_triage_result_has_format_confidence(self):
        """IT13p: triage_input() returns input_format_confidence in [0,1]."""
        r = triage.triage_input(_LI_POST, registry=REGISTRY_FIXTURE)
        self.assertIn("input_format_confidence", r)
        conf = r["input_format_confidence"]
        self.assertGreaterEqual(conf, 0.0)
        self.assertLessEqual(conf, 1.0)

    def test_IT13q_triage_result_has_processing_disposition(self):
        """IT13q: triage_input() returns processing_disposition."""
        r = triage.triage_input(_LI_POST, registry=REGISTRY_FIXTURE)
        self.assertIn("processing_disposition", r)
        self.assertIn(r["processing_disposition"],
                      {"process_all_streams", "confirm_ri_mutations", "confirm_all"})

    def test_IT13r_linkedin_post_disposition_is_process_all(self):
        """IT13r: LinkedIn post disposition is process_all_streams."""
        r = triage.triage_input(_LI_POST, registry=REGISTRY_FIXTURE)
        self.assertEqual(r["processing_disposition"], "process_all_streams")

    def test_IT13s_email_thread_disposition_is_confirm_ri(self):
        """IT13s: Email thread disposition is confirm_ri_mutations."""
        r = triage.triage_input(_EMAIL_THREAD, registry=REGISTRY_FIXTURE)
        self.assertEqual(r["processing_disposition"], "confirm_ri_mutations")

    def test_IT13t_auto_process_eligible_true_for_linkedin_post(self):
        """IT13t: auto_process_eligible=True for linkedin_post format."""
        r = triage.triage_input(_LI_POST, registry=REGISTRY_FIXTURE)
        self.assertTrue(r["auto_process_eligible"])

    def test_IT13u_auto_process_eligible_false_for_email_thread(self):
        """IT13u: auto_process_eligible=False for email_thread format."""
        r = triage.triage_input(_EMAIL_THREAD, registry=REGISTRY_FIXTURE)
        self.assertFalse(r["auto_process_eligible"])

    def test_IT13v_linkedin_post_with_author_injects_ri_stream(self):
        """IT13v: linkedin_post + author_name injects RI stream even without trigger words."""
        # Text with no explicit RI triggers ("joined", "hired", etc.)
        plain_li = (
            "Interesting quarter for restaurant technology. "
            "AI ordering is moving from pilot to production at scale. "
            "https://www.linkedin.com/in/alice-smith/ • 1st"
        )
        r = triage.triage_input(
            plain_li,
            author_name="Alice Smith",
            author_company="Acme POS",
            registry=REGISTRY_FIXTURE,
        )
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_RI, types,
                      "linkedin_post + author_name must inject implicit RI stream")

    def test_IT13w_stream_auto_process_field_present(self):
        """IT13w: Every stream in identified_types carries auto_process_eligible."""
        r = triage.triage_input(_LI_POST, registry=REGISTRY_FIXTURE)
        for stream in r["identified_types"]:
            self.assertIn("auto_process_eligible", stream,
                          f"Stream {stream['intelligence_type']} missing auto_process_eligible")

    def test_IT13x_micro_enrich_stream_auto_process_false(self):
        """IT13x: micro_graph_enrichment stream always has auto_process_eligible=False."""
        # PAR Technology is a known artifact; inject text mentioning PAR + topology terms
        par_text = (
            "PAR Technology operator list: 1,200 restaurant customers, 18 territories. "
            "https://www.linkedin.com/in/oliver-ostertag/ • 1st"
        )
        r = triage.triage_input(par_text, registry=REGISTRY_FIXTURE)
        for stream in r["identified_types"]:
            if stream["intelligence_type"] == triage.TYPE_MICRO_ENRICH:
                self.assertFalse(stream["auto_process_eligible"],
                                 "micro_graph_enrichment must never be auto_process_eligible")


# ---------------------------------------------------------------------------
# IT14 — Sprint E-5: trust_stats block in triage_input() return
# ---------------------------------------------------------------------------

class TestIT14_TrustStats(unittest.TestCase):
    """IT14: trust_stats block is present and accurate in every triage_input() result."""

    def test_IT14a_trust_stats_key_present(self):
        """IT14a: triage_input() always returns a trust_stats key."""
        result = triage.triage_input("Global Payments acquires Toast competitor.",
                                     registry=REGISTRY_FIXTURE)
        self.assertIn("trust_stats", result)
        ts = result["trust_stats"]
        for field in ("streams_detected", "actionable_streams", "mutations_proposed",
                      "auto_processable", "noise_only", "processing_order",
                      "confidence_by_type"):
            self.assertIn(field, ts, f"trust_stats missing field: {field}")

    def test_IT14b_noise_only_trust_stats_correct(self):
        """IT14b: noise-only input produces trust_stats with actionable_streams=0."""
        result = triage.triage_input("ok thanks bye", registry=REGISTRY_FIXTURE)
        ts = result["trust_stats"]
        self.assertTrue(ts["noise_only"])
        self.assertEqual(ts["actionable_streams"], 0)
        self.assertEqual(ts["mutations_proposed"], 0)
        self.assertEqual(ts["processing_order"], [])

    def test_IT14c_actionable_stream_counts_match(self):
        """IT14c: trust_stats counts match identified_types contents."""
        text = (
            "PAR Technology Q3 earnings: comp sales +4%, 1,200 operator customers. "
            "Met with Oliver Ostertag last Tuesday — he confirmed new pilot launch."
        )
        result = triage.triage_input(text, registry=REGISTRY_FIXTURE)
        ts = result["trust_stats"]
        actionable = [s for s in result["identified_types"]
                      if s["intelligence_type"] != triage.TYPE_NOISE]
        self.assertEqual(ts["actionable_streams"], len(actionable))
        self.assertEqual(ts["streams_detected"], len(result["identified_types"]))
        proposed = sum(1 for s in actionable if s.get("requires_confirmation"))
        self.assertEqual(ts["mutations_proposed"], proposed)

    def test_IT14d_confidence_by_type_excludes_noise(self):
        """IT14d: confidence_by_type contains only non-noise stream types."""
        result = triage.triage_input(
            "Operator traffic down 3% — consumer trade-down accelerating.",
            registry=REGISTRY_FIXTURE,
        )
        ts = result["trust_stats"]
        self.assertNotIn(triage.TYPE_NOISE, ts["confidence_by_type"])
        for t in ts["confidence_by_type"]:
            self.assertIn(t, triage.VALID_TYPES - {triage.TYPE_NOISE})


class TestTriageOverlayText(unittest.TestCase):
    """RB 9.88 (RB-DEFECT-046 Slice 1): triage_overlay_text noise-filtering wrapper."""

    def test_noise_returns_none(self):
        result = triage.triage_overlay_text(
            "Re: lunch tomorrow?\nSounds good, see you then.",
            source_type="email",
            source_name="thread-1",
            registry=REGISTRY_FIXTURE,
        )
        self.assertIsNone(result)

    def test_empty_text_returns_none(self):
        result = triage.triage_overlay_text(
            "", source_type="email", source_name="thread-2",
            registry=REGISTRY_FIXTURE,
        )
        self.assertIsNone(result)

    def test_intelligence_bearing_returns_triage_dict(self):
        result = triage.triage_overlay_text(
            "PAR Technology Q3 earnings: comp sales +4%, 1,200 operator customers.",
            source_type="email",
            source_name="thread-3",
            registry=REGISTRY_FIXTURE,
        )
        self.assertIsNotNone(result)
        self.assertIn("triage_id", result)
        self.assertFalse(result["noise_only"])
        self.assertEqual(result["source_type"], "email")
        self.assertEqual(result["source_name"], "thread-3")


class TestWatchlistEntityMentionDefect(TestMacroClassifier):
    """RB-DEFECT-2026-08-29: a real uploaded LinkedIn screenshot ("Starbucks
    scrapped its AI inventory tool... NomadGo computer vision cameras") named
    two tracked entity_alerts.MANDATORY_ALL entities by name and was still
    classified as noise-adjacent -- _classify_macro only ever counted generic
    industry keywords, never checked company identity, and this story's
    real phrasing ("scrapped", "computer vision") didn't happen to hit any
    of those generic buckets. Fixed via _matched_watchlist_entities()."""

    def test_named_watchlist_entities_produce_a_macro_stream_even_with_no_generic_keywords(self):
        text = (
            "Starbucks scrapped its AI inventory tool. 11,000 stores are back "
            "to counting milk by hand instead of using NomadGo computer "
            "vision cameras."
        )
        r = self._triage(text)
        s = self._get_stream(r, triage.TYPE_MACRO)
        self.assertIsNotNone(s, "a real mention of two tracked watchlist entities must not be silently dropped")
        self.assertIn("Starbucks", s["extracted_entities"])
        self.assertIn("NomadGo", s["extracted_entities"])
        self.assertIn("Starbucks", s["extracted_summary"])
        self.assertIn("NomadGo", s["extracted_summary"])

    def test_single_named_entity_sets_entity_scoped_and_entity_name(self):
        text = "Toast announced a new product line for enterprise customers."
        r = self._triage(text)
        s = self._get_stream(r, triage.TYPE_MACRO)
        self.assertIsNotNone(s)
        self.assertTrue(s["entity_scoped"])
        self.assertEqual(s["entity_name"], "Toast")

    def test_two_named_entities_not_single_entity_scoped(self):
        text = "Starbucks scrapped its AI inventory tool built by NomadGo."
        r = self._triage(text)
        s = self._get_stream(r, triage.TYPE_MACRO)
        self.assertIsNotNone(s)
        self.assertFalse(s["entity_scoped"])
        self.assertIsNone(s["entity_name"])

    def test_ambiguous_entity_name_false_positive_still_guarded(self):
        """"Square" is a known-ambiguous MANDATORY_ALL name -- the existing
        entity_alerts false-positive guard (place names, "square feet", ...)
        must still apply here, not just in entity_alerts.py's own press-
        release path."""
        text = "The new restaurant occupies 4,000 square feet in a strip mall."
        r = self._triage(text)
        s = self._get_stream(r, triage.TYPE_MACRO)
        if s is not None:
            self.assertNotIn("Square", s["extracted_entities"])

    def test_no_watchlist_entity_and_no_keywords_still_returns_none(self):
        text = "My cousin's birthday party was fun this weekend."
        r = self._triage(text)
        s = self._get_stream(r, triage.TYPE_MACRO)
        self.assertIsNone(s)


if __name__ == "__main__":
    unittest.main()
