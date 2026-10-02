"""
test_loop_owner_and_actions.py — RB 9.71 item 4 (RB-DEFECT-040 #7): loop
records carry an `owner` and `days_overdue`, and overdue loops past
`_LOOP_ESCALATE_AFTER_DAYS` get an "escalate" recommended action + action
option instead of an indefinite "close or re-date".
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import intelligence_lifecycle as ilc  # noqa: E402

_TODAY = date(2026, 6, 13)


def _base_report(loops: dict) -> dict:
    return {
        "today": _TODAY.isoformat(),
        "relationship_signals": {"signals": [], "stale_sources": []},
        "loops": loops,
        "active_threads": [],
        "market_signals": {"signals": [], "freshness_status": "ok"},
        "strategic_memory": {},
        "daily_prep_summary": {
            "totals": {"inner": 0, "broader": 0, "dormant_valuable": 0},
            "source_health": {"sources": {}},
            "signals": [],
            "overdue_loops": [],
            "stale_sources": [],
            "meeting_prep": [],
        },
    }


def _loop(loop_id, party, target, opened=None, status_raw="open", closed=False):
    return {
        "id": loop_id,
        "opened": opened or (target - timedelta(days=14)),
        "party": party,
        "description": f"Follow up with {party}",
        "target": target,
        "status_raw": status_raw,
        "closed": closed,
    }


def _build_brief(loops):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_store = Path(tmpdir) / "intelligence_store.json"
        orig_store_path = ilc._store_path
        ilc._store_path = lambda: tmp_store
        try:
            return db.build_canonical_brief(_base_report(loops))
        finally:
            ilc._store_path = orig_store_path


def _loops_section(canon):
    return canon.get("sections", {}).get("loops_and_obligations") or []


class TestLoopOwnerField(unittest.TestCase):
    def test_loop_items_have_todd_owner(self):
        loops = {
            "overdue": [_loop("L-001", "Acme Corp", _TODAY - timedelta(days=2))],
            "due_today": [],
            "this_week": [],
        }
        canon = _build_brief(loops)
        items = [i for i in _loops_section(canon) if "L-001" in i["title"]]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["extras"]["owner"], "Todd")


class TestLoopEscalation(unittest.TestCase):
    def test_mildly_overdue_loop_gets_close_or_redate(self):
        loops = {
            "overdue": [_loop("L-002", "Beta LLC", _TODAY - timedelta(days=2))],
            "due_today": [],
            "this_week": [],
        }
        canon = _build_brief(loops)
        item = next(i for i in _loops_section(canon) if "L-002" in i["title"])
        self.assertIn("Close or re-date", item["recommended_action"])
        self.assertEqual(item["extras"]["days_overdue"], 2)
        self.assertNotIn("escalate", item["action_options"])

    def test_severely_overdue_loop_gets_escalated(self):
        loops = {
            "overdue": [_loop("L-003", "Gamma Inc", _TODAY - timedelta(days=10))],
            "due_today": [],
            "this_week": [],
        }
        canon = _build_brief(loops)
        item = next(i for i in _loops_section(canon) if "L-003" in i["title"])
        self.assertIn("Escalate", item["recommended_action"])
        self.assertIn("10 days overdue", item["recommended_action"])
        self.assertEqual(item["extras"]["days_overdue"], 10)
        self.assertIn("escalate", item["action_options"])
        self.assertIn("convert_to_task", item["action_options"])

    def test_due_today_loop_unaffected(self):
        loops = {
            "overdue": [],
            "due_today": [_loop("L-004", "Delta Co", _TODAY)],
            "this_week": [],
        }
        canon = _build_brief(loops)
        item = next(i for i in _loops_section(canon) if "L-004" in i["title"])
        self.assertEqual(item["extras"]["days_overdue"], 0)
        self.assertIn("Prepare next step for L-004", item["recommended_action"])


if __name__ == "__main__":
    unittest.main()
