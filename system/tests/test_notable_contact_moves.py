"""
test_notable_contact_moves.py

Regression coverage: Section C showed "[EXEC HIRE] Dairy Queen names Phil
Crawford chief technology officer" as generic industry news with no check
against what RB already knows about that person. RB already tracked a Phil
Crawford in the baseline (Adyen, Global Head F&B + Hospitality) -- exactly
the kind of relationship-intelligence event RB exists to catch, especially
for a CTO, but exec-hire headlines were never cross-referenced.

_compute_notable_contact_moves scans restaurant_industry_headlines and
restaurant_technology_headlines for "[EXEC HIRE]"-badged items, extracts the
named person via _EXEC_HIRE_NAME_RE, and looks them up against the
baseline by exact normalized name -- silently, only surfacing a match.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402


def _exec_hire_item(title: str) -> dict:
    return {
        "title": f"[👤 EXEC HIRE] {title}",
        "extras": {"signal_badge": "[👤 EXEC HIRE]", "source_url": "https://example.com/exec-hire"},
    }


_FAKE_BASELINE = [
    {"id": "phil-crawford", "name": "Phil Crawford", "current_company": "Adyen",
     "current_role": "Global Head F&B + Hospitality"},
    {"id": "florence-ho", "name": "Florence Ho", "current_company": "", "current_role": ""},
]


class TestExecHireNameExtraction(unittest.TestCase):
    def test_names_pattern_extracted(self):
        m = db._EXEC_HIRE_NAME_RE.match("Dairy Queen names Phil Crawford chief technology officer")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("person"), "Phil Crawford")
        self.assertEqual(m.group("company"), "Dairy Queen")
        self.assertIn("chief technology officer", m.group("role").lower())

    def test_appoints_pattern_extracted(self):
        m = db._EXEC_HIRE_NAME_RE.match("Firebirds Wood Fired Grill appoints Florence Ho chief marketing officer")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("person"), "Florence Ho")


class TestNotableContactMoves(unittest.TestCase):
    def test_baseline_match_surfaced_with_prior_context(self):
        sections = {
            "restaurant_industry_headlines": [
                _exec_hire_item("Dairy Queen names Phil Crawford chief technology officer"),
            ],
            "restaurant_technology_headlines": [],
        }
        with patch.object(db.core, "load_baseline", return_value=_FAKE_BASELINE):
            items = db._compute_notable_contact_moves(sections)
        self.assertEqual(len(items), 1)
        extras = items[0]["extras"]
        self.assertEqual(extras["person"], "Phil Crawford")
        self.assertEqual(extras["new_company"], "Dairy Queen")
        self.assertTrue(extras["is_cto"])
        self.assertIn("Adyen", items[0]["why_it_matters"])

    def test_no_baseline_match_yields_nothing(self):
        sections = {
            "restaurant_industry_headlines": [
                _exec_hire_item("Some Chain names Nobody Known chief financial officer"),
            ],
            "restaurant_technology_headlines": [],
        }
        with patch.object(db.core, "load_baseline", return_value=_FAKE_BASELINE):
            items = db._compute_notable_contact_moves(sections)
        self.assertEqual(items, [])

    def test_non_exec_hire_badge_ignored(self):
        sections = {
            "restaurant_industry_headlines": [
                {"title": "[💰 FUNDING] Phil Crawford's company raises new round",
                 "extras": {"signal_badge": "[💰 FUNDING]"}},
            ],
            "restaurant_technology_headlines": [],
        }
        with patch.object(db.core, "load_baseline", return_value=_FAKE_BASELINE):
            items = db._compute_notable_contact_moves(sections)
        self.assertEqual(items, [])

    def test_non_cto_role_still_matches_but_flagged_differently(self):
        sections = {
            "restaurant_industry_headlines": [
                _exec_hire_item("Firebirds Wood Fired Grill appoints Florence Ho chief marketing officer"),
            ],
            "restaurant_technology_headlines": [],
        }
        with patch.object(db.core, "load_baseline", return_value=_FAKE_BASELINE):
            items = db._compute_notable_contact_moves(sections)
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["extras"]["is_cto"])


if __name__ == "__main__":
    unittest.main()
