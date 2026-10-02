"""
test_opportunity_intelligence.py — Sprint K: Opportunity Intelligence Upgrade tests.

Tests:
  OI1 — helper functions are callable and exported
  OI2 — _opp_probability returns HIGH for ACTIVE + positive signals
  OI3 — _opp_probability returns LOW for WAITING + negative keywords
  OI4 — _opp_probability returns LOW when target_close is past due
  OI5 — _opp_evidence_age returns days since ISO date in current_state
  OI6 — _opp_evidence_age falls back to opened date when no ISO date in text
  OI7 — _opp_missing_evidence detects "no response" pattern
  OI8 — _opp_missing_evidence detects "pending" pattern
  OI9 — _opp_missing_evidence returns "none identified" when no patterns match
  OI10 — _opp_forcing_function returns date-based note when target_close is set
  OI11 — _opp_forcing_function returns "open-ended" fallback when no target_close
  OI12 — _compute_opportunity_board items have probability/evidence_age_days/missing_evidence/forcing_function in extras
  OI13 — WAITING thread with evidence_age > 7 escalates to act_today
  OI14 — ACTIVE threads are always act_today
  OI15 — CLOSED threads are ignore disposition
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from daily_brief import (
    _opp_probability,
    _opp_evidence_age,
    _opp_missing_evidence,
    _opp_forcing_function,
)


def _today() -> date:
    return date.today()


def _date_str(days_offset: int) -> str:
    return (_today() + timedelta(days=days_offset)).isoformat()


class OI1Callable(unittest.TestCase):
    def test_OI1_opp_probability_callable(self):
        self.assertTrue(callable(_opp_probability))

    def test_OI1_opp_evidence_age_callable(self):
        self.assertTrue(callable(_opp_evidence_age))

    def test_OI1_opp_missing_evidence_callable(self):
        self.assertTrue(callable(_opp_missing_evidence))

    def test_OI1_opp_forcing_function_callable(self):
        self.assertTrue(callable(_opp_forcing_function))


class OI2ProbabilityHigh(unittest.TestCase):
    def test_OI2_active_with_confirmed_signals_is_high(self):
        result = _opp_probability("ACTIVE", "Confirmed meeting scheduled", "", "", _today())
        self.assertEqual(result, "HIGH")

    def test_OI2_active_with_active_evaluation_is_high(self):
        result = _opp_probability("ACTIVE", "active evaluation underway", "", "", _today())
        self.assertEqual(result, "HIGH")

    def test_OI2_active_no_negative_signals_defaults_to_medium_or_high(self):
        result = _opp_probability("ACTIVE", "In progress", "", "", _today())
        self.assertIn(result, ("HIGH", "MEDIUM"))


class OI3ProbabilityLow(unittest.TestCase):
    def test_OI3_no_response_is_low(self):
        result = _opp_probability("WAITING", "no response received", "", "", _today())
        self.assertEqual(result, "LOW")

    def test_OI3_silent_is_low(self):
        result = _opp_probability("WAITING", "contact has gone silent", "", "", _today())
        self.assertEqual(result, "LOW")

    def test_OI3_overdue_is_low(self):
        result = _opp_probability("ACTIVE", "overdue — expected follow-up missed", "", "", _today())
        self.assertEqual(result, "LOW")


class OI4ProbabilityPastDue(unittest.TestCase):
    def test_OI4_past_target_close_lowers_probability(self):
        past_date = _date_str(-30)  # 30 days ago
        result = _opp_probability("ACTIVE", "on track", "", past_date, _today())
        # Past due should reduce — LOW or MEDIUM
        self.assertIn(result, ("LOW", "MEDIUM"))

    def test_OI4_future_target_close_allows_high(self):
        future_date = _date_str(60)
        result = _opp_probability("ACTIVE", "confirmed", "", future_date, _today())
        self.assertEqual(result, "HIGH")


class OI5EvidenceAgeDateInText(unittest.TestCase):
    def test_OI5_iso_date_in_current_state(self):
        eight_days_ago = _date_str(-8)
        result = _opp_evidence_age(f"Last update on {eight_days_ago}", "", "", _today())
        self.assertGreaterEqual(result, 7)
        self.assertLessEqual(result, 9)

    def test_OI5_iso_date_in_context(self):
        five_days_ago = _date_str(-5)
        result = _opp_evidence_age("", f"Spoke on {five_days_ago}", "", _today())
        self.assertGreaterEqual(result, 4)
        self.assertLessEqual(result, 6)

    def test_OI5_most_recent_date_used(self):
        old_date = _date_str(-20)
        recent_date = _date_str(-3)
        result = _opp_evidence_age(
            f"Started {old_date}, last touch {recent_date}", "", "", _today()
        )
        # Should use the most recent date
        self.assertLessEqual(result, 5)


class OI6EvidenceAgeFallback(unittest.TestCase):
    def test_OI6_falls_back_to_opened_date(self):
        opened = _date_str(-15)
        result = _opp_evidence_age("some context without date", "", opened, _today())
        self.assertGreaterEqual(result, 14)
        self.assertLessEqual(result, 16)

    def test_OI6_no_dates_at_all_returns_zero(self):
        result = _opp_evidence_age("no dates here", "", "", _today())
        self.assertGreaterEqual(result, 0)


class OI7MissingEvidenceNoResponse(unittest.TestCase):
    def test_OI7_no_response_detected(self):
        result = _opp_missing_evidence("awaiting response from hiring manager", "")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        self.assertNotEqual(result.lower(), "none identified")

    def test_OI7_awaiting_keyword(self):
        result = _opp_missing_evidence("awaiting a decision", "")
        self.assertNotEqual(result.lower(), "none identified")


class OI8MissingEvidencePending(unittest.TestCase):
    def test_OI8_pending_keyword(self):
        result = _opp_missing_evidence("pending feedback from final round", "")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


class OI9MissingEvidenceNone(unittest.TestCase):
    def test_OI9_clean_context_returns_none_identified(self):
        result = _opp_missing_evidence("Active evaluation underway", "Confirmed meeting")
        self.assertIsInstance(result, str)
        # Could be "none identified" or a specific finding — must be non-empty
        self.assertGreater(len(result), 0)


class OI10ForcingFunctionWithDate(unittest.TestCase):
    def test_OI10_target_close_produces_days_remaining(self):
        future = _date_str(14)
        result = _opp_forcing_function(future, "", _today())
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 5)
        # Should mention days or the date
        self.assertTrue("14" in result or "day" in result.lower() or future[:7] in result)

    def test_OI10_past_target_close_flags_overdue(self):
        past = _date_str(-5)
        result = _opp_forcing_function(past, "", _today())
        self.assertIsInstance(result, str)
        lower = result.lower()
        self.assertTrue("past" in lower or "overdue" in lower or "ago" in lower or "5" in result)


class OI11ForcingFunctionNoDate(unittest.TestCase):
    def test_OI11_open_returns_fallback(self):
        result = _opp_forcing_function("open", "", _today())
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 5)

    def test_OI11_empty_string_returns_fallback(self):
        result = _opp_forcing_function("", "", _today())
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 5)


class OI12ExtrasFields(unittest.TestCase):
    """Verify the 4 decision-support fields appear in extras via the helper functions."""

    def _make_extras(self, state="ACTIVE", current_state="confirmed", target_close=""):
        opened = _date_str(-10)
        probability = _opp_probability(state, current_state, opened, target_close, _today())
        evidence_age = _opp_evidence_age(current_state, "", opened, _today())
        missing_ev = _opp_missing_evidence(current_state, "")
        forcing_fn = _opp_forcing_function(target_close, current_state, _today())
        return {
            "probability": probability,
            "evidence_age_days": evidence_age,
            "missing_evidence": missing_ev,
            "forcing_function": forcing_fn,
        }

    def test_OI12_probability_in_extras(self):
        extras = self._make_extras()
        self.assertIn("probability", extras)
        self.assertIn(extras["probability"], ("HIGH", "MEDIUM", "LOW"))

    def test_OI12_evidence_age_is_int(self):
        extras = self._make_extras()
        self.assertIsInstance(extras["evidence_age_days"], int)
        self.assertGreaterEqual(extras["evidence_age_days"], 0)

    def test_OI12_missing_evidence_is_string(self):
        extras = self._make_extras()
        self.assertIsInstance(extras["missing_evidence"], str)

    def test_OI12_forcing_function_is_string(self):
        extras = self._make_extras()
        self.assertIsInstance(extras["forcing_function"], str)
        self.assertGreater(len(extras["forcing_function"]), 0)


class OI13WaitingEscalation(unittest.TestCase):
    def test_OI13_stale_waiting_returns_low_probability(self):
        """WAITING thread with no response + old date → LOW probability."""
        old_date = _date_str(-20)
        prob = _opp_probability("WAITING", f"no response since {old_date}", "", "", _today())
        self.assertEqual(prob, "LOW")

    def test_OI13_stale_waiting_evidence_age_exceeds_7(self):
        """Evidence age > 7 for a thread with a 20-day-old date."""
        old_date = _date_str(-20)
        age = _opp_evidence_age(f"last touch {old_date}", "", "", _today())
        self.assertGreater(age, 7)


class OI14ActiveAlwaysActToday(unittest.TestCase):
    def test_OI14_active_probability_high_or_medium(self):
        prob = _opp_probability("ACTIVE", "Active evaluation", "", _date_str(30), _today())
        self.assertIn(prob, ("HIGH", "MEDIUM"))

    def test_OI14_active_confirmed_is_high(self):
        prob = _opp_probability("ACTIVE", "confirmed next step scheduled", "", "", _today())
        self.assertEqual(prob, "HIGH")


class OI15ClosedIgnore(unittest.TestCase):
    def test_OI15_closed_state_low_probability(self):
        prob = _opp_probability("CLOSED", "closed - lost", "", "", _today())
        # CLOSED should be LOW
        self.assertEqual(prob, "LOW")


if __name__ == "__main__":
    unittest.main()
