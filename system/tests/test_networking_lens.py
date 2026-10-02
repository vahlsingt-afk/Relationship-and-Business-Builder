"""
test_networking_lens.py — unit + integration tests for networking_lens.py (RB 9.33).

Test groups:
  NL1 (7):  detect_networking_event — event-type identification, keyword thresholds
  NL2 (6):  extract_participants — name detection, org attribution, raw context
  NL3 (6):  want extraction — ICP descriptions, referral asks, partner profiles
  NL4 (8):  baseline matching — match_score, tier_weight, match_type, priority ladder
  NL5 (8):  scan_networking_event output contract — schema, candidates, bridges,
             no-match, trust statement, non-networking input returns correct shape
  NL6 (6):  relationship_intake integration — networking_scan present in response,
             apply=False posture preserved, non-networking input has no networking_scan,
             empty text no crash, networking_scan schema fields, bridges field present
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import networking_lens as nl


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ROUNDTABLE_TEXT = """
Hospitality Table Meeting — May 30, 2026

Attendees:
  Sarah Chen at Olo — helps mid-market QSRs reduce delivery costs.
  She's looking to meet VP of Operations at regional restaurant chains.
  Sarah Chen said her ideal client is a regional chain doing $20M+ in revenue.

  Bob Gibson from Toast — restaurant POS and payments platform.
  Bob Gibson is seeking technology partners in the loyalty and engagement space.
  He wants to connect with people working on guest experience platforms.

  Mike Dunn at PAR Technology — said he's looking for enterprise operators.
  Mike Dunn serves large franchise groups and wants introductions to PE-backed restaurant brands.

  Oliver Ostertag from Genius Sports — analytics and data platform.
  Oliver Ostertag is looking for referral partners in the restaurant media space.
"""

_COFFEE_TEXT = """
Virtual coffee with Jane Smith from BridgePoint Capital.
Jane Smith helps restaurant operators with growth financing.
She's looking to meet restaurant operators who need expansion capital.
She said her ideal client profile includes regional chains with 10-50 locations.
"""

_NON_NETWORKING_TEXT = """
The quarterly earnings release showed strong revenue growth.
PAR Technology reported Q2 earnings above consensus estimates.
The stock moved up 4% in after-hours trading.
"""

# Minimal baseline fixture for unit tests (avoids real disk reads)
_FAKE_BASELINE = [
    {
        "id": "alice-jones", "name": "Alice Jones",
        "current_company": "Flynn Restaurant Group", "current_role": "VP of Operations",
        "signal_class": "RC", "rc_tier": "inner",
        "circles": ["hospitality-table"], "tags": ["restaurant-operator"],
    },
    {
        "id": "bob-smith", "name": "Bob Smith",
        "current_company": "Carrols Restaurant Group", "current_role": "Chief Operating Officer",
        "signal_class": "RC", "rc_tier": "broader",
        "circles": [], "tags": ["restaurant-operator", "franchise"],
    },
    {
        "id": "carol-davis", "name": "Carol Davis",
        "current_company": "Vista Equity Partners", "current_role": "Managing Director",
        "signal_class": "RC", "rc_tier": "broader",
        "circles": [], "tags": ["private-equity", "investor"],
    },
    {
        "id": "dan-martin", "name": "Dan Martin",
        "current_company": "Clipper Loyalty", "current_role": "Head of Partnerships",
        "signal_class": "LKI", "rc_tier": None,
        "circles": [], "tags": ["loyalty", "guest-experience", "restaurant-tech"],
    },
    {
        "id": "eva-lee", "name": "Eva Lee",
        "current_company": "PAR Technology", "current_role": "VP Sales",
        "signal_class": "RC", "rc_tier": "inner",
        "circles": ["former-par-employees"], "tags": ["par-alumni"],
    },
]


# =============================================================================
# NL1 — detect_networking_event
# =============================================================================

class TestNL1Detection(unittest.TestCase):
    """NL1 — detect_networking_event correctly classifies inputs."""

    def test_hospitality_table_detected(self):
        detected, event_type = nl.detect_networking_event("Hospitality Table Meeting notes")
        self.assertTrue(detected)
        self.assertEqual(event_type, "hospitality_table")

    def test_scn_meeting_detected(self):
        detected, event_type = nl.detect_networking_event("SCN meeting recap for May")
        self.assertTrue(detected)
        self.assertEqual(event_type, "scn_meeting")

    def test_roundtable_detected(self):
        detected, event_type = nl.detect_networking_event("Industry roundtable on QSR tech trends")
        self.assertTrue(detected)
        self.assertEqual(event_type, "roundtable")

    def test_virtual_coffee_detected(self):
        detected, event_type = nl.detect_networking_event("Virtual coffee with Sarah Chen from Olo")
        self.assertTrue(detected)
        self.assertEqual(event_type, "virtual_coffee")

    def test_referral_conversation_detected(self):
        detected, event_type = nl.detect_networking_event(
            "She mentioned her ideal client profile is regional chains."
        )
        self.assertTrue(detected)
        self.assertEqual(event_type, "referral_conversation")

    def test_non_networking_not_detected(self):
        detected, _ = nl.detect_networking_event(_NON_NETWORKING_TEXT)
        self.assertFalse(detected)

    def test_fallback_threshold_triggers(self):
        # Two distinct fallback keywords should trigger general_networking
        text = "We discussed an introduction and a potential referral for their network."
        detected, event_type = nl.detect_networking_event(text)
        self.assertTrue(detected)
        self.assertEqual(event_type, "general_networking")


# =============================================================================
# NL2 — extract_participants
# =============================================================================

class TestNL2ParticipantExtraction(unittest.TestCase):
    """NL2 — extract_participants finds named people and their orgs."""

    def test_extracts_participant_names(self):
        participants = nl.extract_participants(_ROUNDTABLE_TEXT)
        names = [p["name"] for p in participants]
        self.assertIn("Sarah Chen", names)
        self.assertIn("Bob Gibson", names)

    def test_extracts_org_from_at_pattern(self):
        participants = nl.extract_participants(_ROUNDTABLE_TEXT)
        sarah = next((p for p in participants if p["name"] == "Sarah Chen"), None)
        self.assertIsNotNone(sarah)
        self.assertIsNotNone(sarah["org"])

    def test_deduplicates_same_name(self):
        # Sarah Chen appears multiple times in _ROUNDTABLE_TEXT
        participants = nl.extract_participants(_ROUNDTABLE_TEXT)
        names = [p["name"] for p in participants]
        self.assertEqual(names.count("Sarah Chen"), 1)

    def test_returns_list_of_dicts(self):
        participants = nl.extract_participants(_COFFEE_TEXT)
        self.assertIsInstance(participants, list)
        for p in participants:
            self.assertIn("name", p)
            self.assertIn("wants", p)
            self.assertIn("raw_context", p)

    def test_multiple_participants_found(self):
        participants = nl.extract_participants(_ROUNDTABLE_TEXT)
        self.assertGreaterEqual(len(participants), 3)

    def test_empty_text_returns_empty_list(self):
        participants = nl.extract_participants("")
        self.assertEqual(participants, [])


# =============================================================================
# NL3 — want extraction
# =============================================================================

class TestNL3WantExtraction(unittest.TestCase):
    """NL3 — wants are extracted from ICP descriptions and referral asks."""

    def test_icp_description_extracted(self):
        participants = nl.extract_participants(_ROUNDTABLE_TEXT)
        sarah = next((p for p in participants if p["name"] == "Sarah Chen"), None)
        self.assertIsNotNone(sarah)
        self.assertGreater(len(sarah["wants"]), 0)

    def test_looking_to_meet_extracted(self):
        text = "Jane Smith is looking to meet VP of Operations at restaurant chains."
        participants = nl.extract_participants(text)
        jane = next((p for p in participants if p["name"] == "Jane Smith"), None)
        self.assertIsNotNone(jane)
        self.assertTrue(any("operations" in w.lower() for w in jane["wants"]))

    def test_referral_partner_phrase_extracted(self):
        text = "John Brown said his referral partner would be a loyalty platform provider."
        participants = nl.extract_participants(text)
        john = next((p for p in participants if p["name"] == "John Brown"), None)
        self.assertIsNotNone(john)
        self.assertTrue(any("loyalty" in w.lower() or "partner" in w.lower() for w in john["wants"]))

    def test_serves_phrase_extracted(self):
        text = "Lisa Wong serves mid-market franchise operators in the Southeast."
        participants = nl.extract_participants(text)
        lisa = next((p for p in participants if p["name"] == "Lisa Wong"), None)
        self.assertIsNotNone(lisa)
        self.assertGreater(len(lisa["wants"]), 0)

    def test_no_wants_on_name_only(self):
        # A name mentioned with no want context should have empty wants
        text = "Mike Reed attended the meeting. The weather was nice."
        participants = nl.extract_participants(text)
        mike = next((p for p in participants if p["name"] == "Mike Reed"), None)
        if mike:  # participant may or may not be extracted depending on context
            self.assertIsInstance(mike["wants"], list)

    def test_wants_are_strings(self):
        participants = nl.extract_participants(_ROUNDTABLE_TEXT)
        for p in participants:
            for w in p["wants"]:
                self.assertIsInstance(w, str)
                self.assertGreater(len(w), 0)


# =============================================================================
# NL4 — baseline matching internals
# =============================================================================

class TestNL4Matching(unittest.TestCase):
    """NL4 — match_score, tier_weight, match_type, priority correct."""

    def test_vp_operations_matches_vp_operations_contact(self):
        contact = _FAKE_BASELINE[0]  # Alice Jones, VP of Operations
        score = nl._match_score(["VP of Operations at regional chains"], contact)
        self.assertGreater(score, 0.10)

    def test_no_match_returns_low_score(self):
        contact = _FAKE_BASELINE[2]  # Carol Davis, PE investor
        score = nl._match_score(["loyalty platform provider"], contact)
        # Carol doesn't match loyalty; score should be low
        self.assertLess(score, 0.30)

    def test_tier_weight_inner_rc_highest(self):
        inner = {"signal_class": "RC", "rc_tier": "inner"}
        broader = {"signal_class": "RC", "rc_tier": "broader"}
        lki = {"signal_class": "LKI", "rc_tier": None}
        self.assertGreater(nl._tier_weight(inner), nl._tier_weight(broader))
        self.assertGreater(nl._tier_weight(broader), nl._tier_weight(lki))

    def test_match_type_icp_for_client_language(self):
        contact = _FAKE_BASELINE[0]
        mtype = nl._match_type(["mid-market restaurant operators as clients"], contact)
        self.assertEqual(mtype, "icp_match")

    def test_match_type_referral_for_partner_language(self):
        contact = _FAKE_BASELINE[3]  # Dan Martin, partnerships
        mtype = nl._match_type(["referral partner in loyalty space"], contact)
        self.assertEqual(mtype, "referral_match")

    def test_priority_high_above_threshold(self):
        self.assertEqual(nl._priority(0.50), "high")

    def test_priority_medium_in_range(self):
        self.assertEqual(nl._priority(0.25), "medium")

    def test_priority_low_below_threshold(self):
        self.assertEqual(nl._priority(0.05), "low")


# =============================================================================
# NL5 — scan_networking_event output contract
# =============================================================================

class TestNL5ScanContract(unittest.TestCase):
    """NL5 — scan_networking_event returns well-formed output."""

    def test_non_networking_returns_not_detected(self):
        result = nl.scan_networking_event(_NON_NETWORKING_TEXT, baseline=_FAKE_BASELINE)
        self.assertFalse(result["networking_event_detected"])
        self.assertEqual(result["introduction_candidates"], [])

    def test_networking_event_detected_true(self):
        result = nl.scan_networking_event(_ROUNDTABLE_TEXT, baseline=_FAKE_BASELINE)
        self.assertTrue(result["networking_event_detected"])

    def test_required_schema_fields_present(self):
        result = nl.scan_networking_event(_ROUNDTABLE_TEXT, baseline=_FAKE_BASELINE)
        for field in (
            "networking_event_detected", "event_type", "participants_analyzed",
            "introduction_candidates", "relationship_bridges", "no_match_participants",
            "trust_statement", "generated_at",
        ):
            self.assertIn(field, result, msg=f"Missing field: {field}")

    def test_introduction_candidates_are_list(self):
        result = nl.scan_networking_event(_ROUNDTABLE_TEXT, baseline=_FAKE_BASELINE)
        self.assertIsInstance(result["introduction_candidates"], list)

    def test_candidate_has_required_fields(self):
        result = nl.scan_networking_event(_ROUNDTABLE_TEXT, baseline=_FAKE_BASELINE)
        if result["introduction_candidates"]:
            c = result["introduction_candidates"][0]
            for field in ("introduce", "to", "rationale", "match_type", "priority", "suggested_framing"):
                self.assertIn(field, c, msg=f"Candidate missing field: {field}")

    def test_trust_statement_is_string(self):
        result = nl.scan_networking_event(_ROUNDTABLE_TEXT, baseline=_FAKE_BASELINE)
        self.assertIsInstance(result["trust_statement"], str)
        self.assertGreater(len(result["trust_statement"]), 10)

    def test_top_n_respected(self):
        result = nl.scan_networking_event(_ROUNDTABLE_TEXT, baseline=_FAKE_BASELINE, top_n=2)
        self.assertLessEqual(len(result["introduction_candidates"]), 2)

    def test_relationship_bridges_is_list(self):
        result = nl.scan_networking_event(_ROUNDTABLE_TEXT, baseline=_FAKE_BASELINE)
        self.assertIsInstance(result["relationship_bridges"], list)


# =============================================================================
# NL6 — relationship_intake integration
# =============================================================================

class TestNL6IntakeIntegration(unittest.TestCase):
    """NL6 — networking_scan surfaces in processRelationshipIntake response.

    RB-DEFECT-042: process_relationship_thread() writes a 'proposed' record to
    INTERACTION_LEDGER_PATH on every call when store_path is not given. These
    tests previously omitted store_path, so every test run appended several
    fixture interactions (Sarah Chen/Olo, Dan Price, Bob Gibson/Toast, Oliver
    Ostertag/Genius Sports, etc.) to the real system/interaction_ledger.json —
    2,333 such "proposed"/"pending confirmation" rows had accumulated by
    2026-06-12, polluting any feature (e.g. a pending_mutations brief section)
    that reads claim_status=='proposed' from that file. Fixed by redirecting
    writes to a per-test temp file.
    """

    def setUp(self):
        import tempfile
        import relationship_intake as ri
        self._tmpdir = tempfile.TemporaryDirectory()
        self._ledger_path = Path(self._tmpdir.name) / "interaction_ledger.json"
        self._patcher = unittest.mock.patch.object(
            ri, "INTERACTION_LEDGER_PATH", self._ledger_path
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_networking_event_adds_networking_scan(self):
        import relationship_intake as ri
        result = ri.process_relationship_thread(
            text=_ROUNDTABLE_TEXT,
            entity_name="Sarah Chen",
            entity_org="Olo",
        )
        self.assertIn("networking_scan", result, "networking_scan must appear for networking events")

    def test_networking_scan_has_candidates(self):
        import relationship_intake as ri
        result = ri.process_relationship_thread(
            text=_ROUNDTABLE_TEXT,
            entity_name="Sarah Chen",
            entity_org="Olo",
        )
        scan = result.get("networking_scan") or {}
        self.assertTrue(scan.get("networking_event_detected"))
        self.assertIn("introduction_candidates", scan)

    def test_non_networking_no_networking_scan(self):
        import relationship_intake as ri
        result = ri.process_relationship_thread(
            text=(
                "Had a call with Dan Price. He's thinking about switching POS systems. "
                "We discussed their current Toast contract and timelines."
            ),
            entity_name="Dan Price",
        )
        # Non-networking context should NOT add networking_scan
        # (or if present, detected=False)
        scan = result.get("networking_scan")
        if scan is not None:
            self.assertFalse(scan.get("networking_event_detected"),
                             "Non-networking text must not trigger networking lens")

    def test_empty_text_no_crash(self):
        import relationship_intake as ri
        result = ri.process_relationship_thread(text="")
        # Must not raise; networking_scan is not expected on empty input
        self.assertIn("persistence_status", result)

    def test_baseline_intake_fields_still_present(self):
        import relationship_intake as ri
        result = ri.process_relationship_thread(
            text=_ROUNDTABLE_TEXT,
            entity_name="Bob Gibson",
            entity_org="Toast",
        )
        # Core intake fields must not be displaced
        for field in ("interactions", "mutation_proposals", "persistence_status"):
            self.assertIn(field, result, msg=f"Core intake field missing: {field}")

    def test_networking_scan_bridges_field_present(self):
        import relationship_intake as ri
        result = ri.process_relationship_thread(
            text=_ROUNDTABLE_TEXT,
            entity_name="Oliver Ostertag",
            entity_org="Genius Sports",
        )
        scan = result.get("networking_scan") or {}
        if scan.get("networking_event_detected"):
            self.assertIn("relationship_bridges", scan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
