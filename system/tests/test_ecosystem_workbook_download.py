"""
test_ecosystem_workbook_download.py — RB-2026-09-01.

Coverage for getEcosystemWorkbookDownloadLink / the /ecosystem-workbook/download
route in rbb_chat.py, wiring up ecosystem_export.py's build_workbook() (built
2026-07-31, "Phase 6") which had zero chat/API exposure until now.

This is route-wiring coverage only -- build_workbook()'s own internals (tab
structure, field-stripping correctness, determinism) are already fully
covered by test_ecosystem_export.py (EXP1-EXP9); this file does not
duplicate that.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"


def _entity(id_: str, name: str, entity_type: str = "brand") -> dict:
    return {"id": id_, "name": name, "entity_type": entity_type}


def _rel(**overrides) -> dict:
    rel = {
        "id": "rel-1",
        "from_entity_id": "brand-checkers",
        "to_entity_id": "vendor-hi-auto",
        "category": "drive_thru_ai",
        "vendor_role": "voice_ai_provider",
        "status": "active",
        "deployment_status": None,
        "deployment": {"stage": "active_rollout", "deployed_units": 120, "penetration_pct": 40.0},
        "confidence": {"level": "high", "rationale": "Internal review: strong primary-source evidence."},
        "relationship_classification": {"level": 4, "level_name": "preferred_vendor"},
        "ai_application": "voice_ai",
        "strategic_note": "Competitive displacement opportunity -- do not share externally.",
        "sources": ["src-hi-auto-1"],
        "updated_at": "2026-07-01T00:00:00Z",
    }
    rel.update(overrides)
    return rel


def _graph(*rels: dict) -> dict:
    return {
        "version": 2,
        "contract": "rb_ecosystem_intelligence_v1",
        "last_updated": "2026-07-31",
        "entities": [
            _entity("brand-checkers", "Checkers & Rally's"),
            _entity("vendor-hi-auto", "Hi Auto", "vendor"),
        ],
        "relationships": list(rels),
    }


class TestEcosystemWorkbookDownload(unittest.TestCase):
    """Exercises the actual route function directly, no live server needed --
    same pattern as test_vendor_list.py's TestVendorListXlsxDownload."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "system" / "api"))
        sys.path.insert(0, str(SCRIPTS_DIR))
        import rbb_chat
        import ecosystem_export

        self.rbb_chat = rbb_chat
        self.ecosystem_export = ecosystem_export
        self._orig_auth = rbb_chat._auth_flexible
        self._orig_load_graph = ecosystem_export.load_graph
        rbb_chat._auth_flexible = lambda *a, **k: None
        ecosystem_export.load_graph = lambda: _graph(_rel())

    def tearDown(self):
        self.rbb_chat._auth_flexible = self._orig_auth
        self.ecosystem_export.load_graph = self._orig_load_graph

    def test_internal_export_includes_strategic_note(self):
        import io
        from openpyxl import load_workbook

        resp = self.rbb_chat.get_ecosystem_workbook_download(
            export_type="internal", passcode=None, x_chat_passcode=None,
        )
        self.assertEqual(
            resp.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertTrue(resp.headers["content-disposition"].endswith('.xlsx"'))
        self.assertIn("internal", resp.headers["content-disposition"])

        wb = load_workbook(io.BytesIO(resp.body))
        self.assertEqual(
            wb.sheetnames,
            [
                "Executive Summary", "Brand Profiles", "Canonical Restaurant Tech Stack",
                "Vendor Customer List", "Evidence Ledger", "Macro Views", "Update Playbook",
            ],
        )
        ws = wb["Canonical Restaurant Tech Stack"]
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        self.assertIn("strategic_note", header)
        self.assertIn("confidence_rationale", header)
        row = [c.value for c in next(ws.iter_rows(min_row=2, max_row=2))]
        note_idx = header.index("strategic_note")
        self.assertEqual(row[note_idx], "Competitive displacement opportunity -- do not share externally.")

    def test_shareable_export_strips_private_fields(self):
        import io
        from openpyxl import load_workbook

        resp = self.rbb_chat.get_ecosystem_workbook_download(
            export_type="shareable", passcode=None, x_chat_passcode=None,
        )
        self.assertIn("shareable", resp.headers["content-disposition"])

        wb = load_workbook(io.BytesIO(resp.body))
        ws = wb["Canonical Restaurant Tech Stack"]
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        self.assertNotIn("strategic_note", header)
        self.assertNotIn("confidence_rationale", header)
        # public facts still present
        self.assertIn("brand", header)
        self.assertIn("vendor", header)

    def test_invalid_export_type_rejected(self):
        with self.assertRaises(Exception):
            self.rbb_chat.get_ecosystem_workbook_download(
                export_type="bogus", passcode=None, x_chat_passcode=None,
            )

    def test_local_operation_rejects_missing_export_type(self):
        status, payload = self.rbb_chat._local_get_ecosystem_workbook_download_link({})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_local_operation_returns_real_download_url(self):
        status, payload = self.rbb_chat._local_get_ecosystem_workbook_download_link(
            {"export_type": "internal"},
        )
        self.assertEqual(status, 200)
        self.assertIn("ecosystem-workbook/download", payload["download_url"])
        self.assertIn("export_type=internal", payload["download_url"])


if __name__ == "__main__":
    unittest.main()
