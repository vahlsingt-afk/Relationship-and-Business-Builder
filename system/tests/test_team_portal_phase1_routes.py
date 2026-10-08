"""
test_team_portal_phase1_routes.py

FastAPI TestClient, route-level coverage for the Phase 1 Market
Intelligence + Sales Tools additions to team_portal_api.py. Same
auth-fixture pattern as test_team_portal.py's TestAuth/TestEcosystemLookup
Routes (isolated MANIFEST_PATH/CREDENTIALS_PATH, never the real credential
store) -- this exists to catch what the unit tests in
test_team_sales_tools.py / test_team_market_intelligence.py can't: that
the routes are wired up correctly, that auth actually gates them, and that
FastAPI serializes the real response shapes (StreamingResponse for the
Blue Sheet download, a plain-text Markdown Response for the Green Sheet
download) without error.

Deliberately reads real, on-disk account data (McDonald's) for the Blue
Sheet routes rather than a fixture account -- read-only, so it's safe, and
it's the same production dossier the earlier manual verification for this
feature (see the Blue Sheet Center masking work) confirmed leaks nothing.
Market Intelligence routes are exercised against isolated fixture files,
matching test_team_market_intelligence.py's own fixtures.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

try:
    from fastapi.testclient import TestClient
    import team_portal_api
    _FASTAPI_OK = True
except ImportError:  # pragma: no cover
    _FASTAPI_OK = False


@unittest.skipUnless(_FASTAPI_OK, "fastapi not installed")
class TestPhase1Routes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)

        self.token = "test-token-phase1"
        creds_path = tmp / "creds.json"
        creds_path.write_text(json.dumps({
            "jsmith": {"token_hash": hashlib.sha256(self.token.encode()).hexdigest(),
                       "created_at": "2026-09-29T00:00:00+00:00", "revoked_at": None},
        }))
        import yaml
        manifest_path = tmp / "manifest.yaml"
        manifest_path.write_text(yaml.safe_dump({
            "members": [{"id": "jsmith", "name": "Jane Smith", "added_at": "2026-09-29T00:00:00+00:00",
                         "revoked_at": None}],
        }))
        self._manifest_patch = patch.object(team_portal_api, "MANIFEST_PATH", manifest_path)
        self._creds_patch = patch.object(team_portal_api, "CREDENTIALS_PATH", creds_path)
        self._manifest_patch.start()
        self._creds_patch.start()
        self.addCleanup(self._manifest_patch.stop)
        self.addCleanup(self._creds_patch.stop)

        # Isolate Market Intelligence's own file reads too, same fixtures
        # test_team_market_intelligence.py uses.
        # RB defect 2026-10-08: published_at was hardcoded to "2026-09-29"
        # instead of relative to "today" -- get_market_news's default
        # days=7 lookback silently excluded this fixture row once more than
        # 7 days had actually elapsed, failing this test with no code
        # change at all (same class of bug as the earnings-monitor bridge
        # fixture fixed in test_earnings_monitor.py the same day).
        today_iso = date.today().isoformat()
        signals_path = tmp / "market_signals.json"
        signals_path.write_text(json.dumps({
            "fetched_at": f"{today_iso}T00:00:00Z", "source_note": "fixture", "items": [{
                "title": "Fixture Co. rolls out new kiosks", "url": "https://example.com/x",
                "source_name": "Fixture Wire", "source_type": "press_release",
                "published_at": today_iso, "company": "Fixture Co.", "side": "operator_demand",
                "category": "kiosk", "pain_point_or_priority": "Public summary.",
                "strategic_relevance": "medium", "restaurant_tech_vendor_implication": "Public GP note.",
                "why_this_matters_to_todd": "TODD-PRIVATE", "recommended_action": "TODD-PRIVATE",
            }],
        }))
        earnings_signals_path = tmp / "market_signals_earnings.jsonl"
        earnings_signals_path.write_text("", encoding="utf-8")
        calendar_path = tmp / "earnings_calendar.yaml"
        calendar_path.write_text(yaml.safe_dump({
            "version": 1, "updated_at": "2026-09-29",
            "companies": [{"name": "Fixture Restaurant Co", "ticker": "FIX", "side": "operator_demand",
                           "category": "pos", "report_months": [3, 6, 9, 12],
                           "last_reported_date": "2026-09-20"}],
        }))
        history_path = tmp / "earnings_calls.jsonl"
        history_path.write_text(json.dumps({
            "company": "Fixture Restaurant Co", "event_date": "2026-09-20", "title": "8-K",
            "signal_dimensions": [], "source_url": "https://example.com/8k", "source_type": "sec_edgar_8k",
            "excerpt": "Public excerpt.",
        }) + "\n", encoding="utf-8")

        self._patches = [
            patch.object(team_portal_api.tmi, "MARKET_SIGNALS_PATH", signals_path),
            patch.object(team_portal_api.tmi, "MARKET_SIGNALS_EARNINGS_PATH", earnings_signals_path),
            patch.object(team_portal_api.tmi, "EARNINGS_CALENDAR_PATH", calendar_path),
            patch.object(team_portal_api.tmi.earnings_monitor, "EARNINGS_HISTORY_PATH", history_path),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        self.client = TestClient(team_portal_api.app)
        self.auth = {"Authorization": f"Bearer {self.token}"}

    # --- auth gating -------------------------------------------------------

    def test_new_routes_require_auth(self):
        for method, path in (
            ("get", "/api/market/news"),
            ("get", "/api/market/earnings"),
            ("get", "/api/sales-tools/blue-sheet/mcdonalds/coverage"),
            ("post", "/api/sales-tools/green-sheet/preview"),
        ):
            resp = getattr(self.client, method)(path)
            self.assertEqual(resp.status_code, 401, f"{method.upper()} {path} did not require auth")

    # --- Market Intelligence ------------------------------------------------

    def test_market_news_route_excludes_todd_private_fields(self):
        resp = self.client.get("/api/market/news", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        blob = json.dumps(items[0])
        self.assertNotIn("TODD-PRIVATE", blob)
        self.assertNotIn("why_this_matters_to_todd", items[0])

    def test_market_news_companies_route(self):
        resp = self.client.get("/api/market/news/companies", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("restaurant_operators", body)
        self.assertIn("restaurant_technology", body)

    def test_market_earnings_route(self):
        resp = self.client.get("/api/market/earnings", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["recently_reported"]), 1)

    def test_market_trends_route(self):
        resp = self.client.get("/api/market/trends", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("trends", resp.json())

    def test_market_earnings_company_detail_unknown_is_404(self):
        resp = self.client.get("/api/market/earnings/Does%20Not%20Exist", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    def test_market_earnings_company_detail_known(self):
        resp = self.client.get("/api/market/earnings/Fixture%20Restaurant%20Co", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["ticker"], "FIX")

    def test_earnings_talking_points_route(self):
        resp = self.client.post(
            "/api/market/earnings/Fixture%20Restaurant%20Co/talking-points", headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["talking_points"])

    # --- Blue Sheet Center ---------------------------------------------------

    def test_blue_sheet_template_route(self):
        resp = self.client.get("/api/sales-tools/blue-sheet/mcdonalds/template", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp.headers["content-type"])

    def test_blue_sheet_coverage_route_known_account(self):
        resp = self.client.get("/api/sales-tools/blue-sheet/mcdonalds/coverage", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("coverage_pct", resp.json())

    def test_blue_sheet_coverage_route_unknown_account_is_404(self):
        resp = self.client.get("/api/sales-tools/blue-sheet/does-not-exist/coverage", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    def test_blue_sheet_download_route_returns_valid_xlsx_and_does_not_leak(self):
        import io
        import openpyxl

        resp = self.client.post("/api/sales-tools/blue-sheet/mcdonalds/download", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp.headers["content-type"])
        self.assertIn("attachment", resp.headers["content-disposition"])

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        blob = "\n".join(
            str(cell.value) for ws in wb.worksheets for row in ws.iter_rows() for cell in row
            if cell.value is not None
        )
        self.assertIn("Team input required", blob)

    def test_blue_sheet_download_route_unknown_account_is_404(self):
        resp = self.client.post("/api/sales-tools/blue-sheet/does-not-exist/download", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    # --- Green Sheet Center ---------------------------------------------------

    def test_green_sheet_preview_route_validation_error_is_422(self):
        resp = self.client.post(
            "/api/sales-tools/green-sheet/preview", headers=self.auth,
            json={"account_slug": "mcdonalds", "call_purpose": "", "attendees": []},
        )
        self.assertEqual(resp.status_code, 422)

    def test_green_sheet_preview_route_unknown_account_is_404(self):
        resp = self.client.post(
            "/api/sales-tools/green-sheet/preview", headers=self.auth,
            json={"account_slug": "does-not-exist", "call_purpose": "Discovery call",
                  "attendees": ["Someone"]},
        )
        self.assertEqual(resp.status_code, 404)

    def test_green_sheet_download_route_returns_markdown(self):
        resp = self.client.post(
            "/api/sales-tools/green-sheet/download", headers=self.auth,
            json={"account_slug": "mcdonalds", "call_purpose": "Discovery call re: DMB",
                  "attendees": ["Bruce Sellnow"]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("markdown", resp.headers["content-type"])
        self.assertIn("Requires human validation", resp.text)

    def test_green_sheet_email_route_success(self):
        """Same generic tpe.send_brief() path as the pre-existing email-
        report/canonical-background-brief-email routes (test_team_portal.
        py's TestEcosystemLookupRoutes already covers that path's success/
        503/502 branches in depth) -- this confirms the Green Sheet route
        reaches it with the right, masked content, not a re-test of send_
        brief's internals."""
        with patch.object(
            team_portal_api.tpe, "send_brief",
            return_value={"sent": True, "status": "smtp_sent", "recipient": "teammate@example.com"},
        ) as mock_send:
            resp = self.client.post(
                "/api/sales-tools/green-sheet/email", headers=self.auth,
                json={"account_slug": "mcdonalds", "call_purpose": "Discovery call re: DMB",
                      "attendees": ["Bruce Sellnow"], "recipient": "teammate@example.com"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["sent"])
        mock_send.assert_called_once()
        self.assertIn("Team input required", mock_send.call_args.kwargs.get("markdown_body", ""))

    def test_green_sheet_email_route_not_configured_returns_503(self):
        with patch.object(team_portal_api.tpe, "send_brief", side_effect=team_portal_api.tpe.NotConfiguredError("no creds")):
            resp = self.client.post(
                "/api/sales-tools/green-sheet/email", headers=self.auth,
                json={"account_slug": "mcdonalds", "call_purpose": "Discovery call re: DMB",
                      "attendees": ["Bruce Sellnow"], "recipient": "teammate@example.com"},
            )
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
