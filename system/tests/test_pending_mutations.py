"""
test_pending_mutations.py — RB 9.69 Option B: pending_mutations daily-brief section.

_compute_pending_mutations() reads system/interaction_ledger.json directly and
surfaces interaction events with claim_status=='proposed' that have sat
unconfirmed for more than 12 hours. This is distinct from graph_mutation_log's
ri_events_recent (24h window over ri_events.jsonl) — see RB-DEFECT-042, which
found 2,333 stale test-fixture 'proposed' entries had accumulated in the real
interaction_ledger.json with no surface ever calling them out.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import rb_core as core  # noqa: E402


def _interaction(contact_id, claim_status, created_at, **extra):
    base = {
        "id": f"ri-{contact_id}-{created_at}",
        "contact_id": contact_id,
        "entity": {"name": contact_id.replace("-", " ").title(), "org": None, "role": None},
        "interaction_date": created_at[:10],
        "source_type": "email",
        "signal_type": "unknown",
        "trust_delta": 0,
        "relationship_state_proposed": "cold",
        "strategic_classification": "Network Contact",
        "recommended_posture": "Monitor",
        "executive_weight": 0,
        "ecosystem_tags": [],
        "who_matters_now_score": 3,
        "source_text_snippet": "test",
        "created_at": created_at,
        "claim_status": claim_status,
        "persistence_status": "pending confirmation" if claim_status == "proposed" else "RB recorded",
        "confirmed_at": None,
    }
    base.update(extra)
    return base


class TestPendingMutations(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmpdir.name) / "interaction_ledger.json"
        self._patcher = patch.object(core, "SYSTEM_DIR", Path(self.tmpdir.name))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmpdir.cleanup()

    def _write_ledger(self, interactions):
        self.ledger_path.write_text(json.dumps({
            "_schema_version": "1.0",
            "interactions": interactions,
        }))

    def test_no_ledger_file_returns_empty(self):
        items = db._compute_pending_mutations({}, {})
        self.assertEqual(items, [])

    def test_no_proposed_entries_returns_green_board(self):
        now = datetime.now(timezone.utc).isoformat()
        self._write_ledger([_interaction("ryan-hildebrand", "confirmed", now)])
        items = db._compute_pending_mutations({}, {})
        self.assertEqual(len(items), 1)
        self.assertIn("none overdue", items[0]["title"])
        self.assertEqual(items[0]["disposition"], "ignore")

    def test_recent_proposed_not_flagged(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_ledger([_interaction("sarah-chen", "proposed", recent)])
        items = db._compute_pending_mutations({}, {})
        self.assertEqual(len(items), 1)
        self.assertIn("none overdue", items[0]["title"])

    def test_stale_proposed_flagged_act_today(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self._write_ledger([
            _interaction("anna-jones", "proposed", old),
            _interaction("bob-gibson", "proposed", recent),
            _interaction("matt", "confirmed", old),
        ])
        items = db._compute_pending_mutations({}, {})
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("1 unconfirmed", item["title"])
        self.assertEqual(item["disposition"], "act_today")
        self.assertEqual(item["extras"]["pending_count"], 1)
        self.assertIn("Anna Jones", item["extras"]["pending_contacts"])
        # confirmProposal (kind="relationship") is the unified GPT action —
        # replaced the old confirmRelationshipInteraction reference, which
        # was never actually in the GPT's action list at all.
        self.assertIn("confirmProposal", item["recommended_action"])

    def test_multiple_stale_proposed_sorted_oldest_first(self):
        oldest = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        older = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        self._write_ledger([
            _interaction("dan-price", "proposed", older),
            _interaction("oliver-ostertag", "proposed", oldest),
        ])
        items = db._compute_pending_mutations({}, {})
        item = items[0]
        self.assertEqual(item["extras"]["pending_count"], 2)
        self.assertEqual(item["extras"]["oldest_created_at"], oldest)
        self.assertEqual(item["extras"]["pending_contacts"][0], "Oliver Ostertag")


if __name__ == "__main__":
    unittest.main()
