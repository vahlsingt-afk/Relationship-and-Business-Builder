"""
test_competitive_landscape_api.py — RB-2026-09-01.

API-layer + export coverage for the competitive-landscape build:
  GET  /competitive-landscape/categories       (listTechStackCategories)
  GET  /competitive-landscape/category/{cat}   (getCategoryMarketShare)
  POST /competitors/{slug}/product-lines       (setCompetitorProductLines)
  POST /competitors/{slug}/gap-points          (addCompetitorGapPoint)
  competitive_landscape_export.build_workbook  (the xlsx renderer)
  competitor_intelligence.sync_all_tracked_competitors (daily-intelligence wiring)
  rbb_chat.py's /competitive-landscape/download route + local operation

Same direct-function-call + monkeypatch isolation pattern as
test_vendor_list.py / test_tech_stack_relationship_api.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


def _real_vendor_graph() -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-01",
        "entities": [
            {"id": "brand-blaze-pizza", "name": "Blaze Pizza", "entity_type": "brand", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
            {"id": "vendor-qu", "name": "Qu", "entity_type": "vendor", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
        ],
        "relationships": [{
            "id": "r1", "from_entity_id": "brand-blaze-pizza", "to_entity_id": "vendor-qu",
            "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
        }],
        "signals": [], "sources": [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


@pytest.fixture()
def isolated_graph(tmp_path: Path, monkeypatch):
    graph_path = tmp_path / "ecosystem_intelligence.json"
    graph_path.write_text(json.dumps(_real_vendor_graph()), encoding="utf-8")
    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    monkeypatch.setattr(server.ecosystem_intelligence.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)

    comp_root = tmp_path / "competitor_intelligence"
    (comp_root / "_portfolio").mkdir(parents=True)
    (comp_root / "_portfolio" / "competitor_registry.json").write_text(json.dumps({"registry": []}), encoding="utf-8")
    monkeypatch.setattr(server.compintel_common, "ROOT", comp_root)
    return graph_path


def _register_competitor(comp_root: Path, slug: str, vendor_entity_id: str) -> None:
    comp_dir = comp_root / "competitors" / slug
    comp_dir.mkdir(parents=True)
    (comp_dir / "competitor.json").write_text(json.dumps({
        "competitor_id": f"comp-{slug}", "competitor_slug": slug, "display_name": slug,
        "vendor_entity_id": vendor_entity_id, "competes_on": [], "aliases": [],
    }), encoding="utf-8")
    (comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")
    reg_path = comp_root / "_portfolio" / "competitor_registry.json"
    reg = json.loads(reg_path.read_text())
    reg["registry"].append({"competitor_slug": slug, "display_name": slug})
    reg_path.write_text(json.dumps(reg), encoding="utf-8")


def test_list_tech_stack_categories_includes_new_categories(isolated_graph):
    result = server.get_tech_stack_categories(x_api_key=None)
    for cat in ("pos_hardware", "drive_thru_timers", "ai_solution_1", "payments_gateway"):
        assert cat in result["categories"]


def test_get_category_market_share_real_data(isolated_graph):
    result = server.get_category_market_share("pos", x_api_key=None)
    assert result["battle_cards"][0]["vendor_name"] == "Qu"
    assert result["battle_cards"][0]["brand_count"] == 1


def test_get_category_market_share_unknown_category_rejected(isolated_graph):
    with pytest.raises(HTTPException) as exc_info:
        server.get_category_market_share("not-a-real-category", x_api_key=None)
    assert exc_info.value.status_code == 400


def test_set_competitor_product_lines_writes_real_value(isolated_graph, tmp_path, monkeypatch):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    result = server.post_set_competitor_product_lines(
        "qu", server.SetCompetitorProductLinesBody(product_lines=["pos", "payments"]), x_api_key=None,
    )
    assert result["competes_on"] == ["payments", "pos"]

    saved = json.loads((comp_root / "competitors" / "qu" / "competitor.json").read_text())
    assert sorted(saved["competes_on"]) == ["payments", "pos"]


def test_set_competitor_product_lines_rejects_unknown_slug(isolated_graph):
    with pytest.raises(HTTPException) as exc_info:
        server.post_set_competitor_product_lines(
            "not-a-real-slug", server.SetCompetitorProductLinesBody(product_lines=["pos"]), x_api_key=None,
        )
    assert exc_info.value.status_code == 404


def test_add_competitor_gap_point_writes_real_sourced_point(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    result = server.post_add_competitor_gap_point(
        "qu",
        server.AddCompetitorGapPointBody(side="genius", point="Real sourced claim.", evidence_id="doc-1"),
        x_api_key=None,
    )
    assert result["vs_genius"]["genius_advantages"][0]["point"] == "Real sourced claim."

    saved = json.loads((comp_root / "competitors" / "qu" / "competitor.json").read_text())
    assert saved["vs_genius"]["genius_advantages"][0]["evidence_id"] == "doc-1"


def test_add_competitor_gap_point_rejects_invalid_side(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    with pytest.raises(HTTPException) as exc_info:
        server.post_add_competitor_gap_point(
            "qu", server.AddCompetitorGapPointBody(side="nobody", point="x"), x_api_key=None,
        )
    assert exc_info.value.status_code == 400


def test_set_category_battle_card_writes_real_value(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    result = server.post_set_category_battle_card(
        "qu", "pos",
        server.SetCategoryBattleCardBody(status="draft", confidence_pct=50, rm_plain_english_posture="talk track"),
        x_api_key=None,
    )
    assert result["battle_card"]["status"] == "draft"
    assert result["battle_card"]["confidence_pct"] == 50

    saved = json.loads((comp_root / "competitors" / "qu" / "competitor.json").read_text())
    assert saved["category_battle_cards"]["pos"]["rm_plain_english_posture"] == "talk track"


def test_set_category_battle_card_rejects_invalid_category(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    with pytest.raises(HTTPException) as exc_info:
        server.post_set_category_battle_card(
            "qu", "not-a-real-category", server.SetCategoryBattleCardBody(status="draft"), x_api_key=None,
        )
    assert exc_info.value.status_code == 400


def test_add_category_battle_card_point_appends_real_point(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    server.post_set_category_battle_card("qu", "pos", server.SetCategoryBattleCardBody(status="draft"), x_api_key=None)
    result = server.post_add_category_battle_card_point(
        "qu", "pos",
        server.AddCategoryBattleCardPointBody(field="discovery_questions", point="Contract renewal date?"),
        x_api_key=None,
    )
    assert result["battle_card"]["discovery_questions"] == ["Contract renewal date?"]


def test_add_category_battle_card_point_requires_card_first(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    with pytest.raises(HTTPException) as exc_info:
        server.post_add_category_battle_card_point(
            "qu", "pos", server.AddCategoryBattleCardPointBody(field="discovery_questions", point="x"), x_api_key=None,
        )
    assert exc_info.value.status_code == 400


def test_get_competitor_profile_includes_pending_reviews(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    review_queue_path = comp_root / "_portfolio" / "review_queue.json"
    review_queue_path.write_text(json.dumps({"pending_reviews": [{
        "review_id": "rev-qu-0001", "competitor_slug": "qu", "category": None,
        "evidence_id": "eco-sig-1", "kind": "material_signal_review", "reason": "New signal.",
        "current_status": None, "status": "pending", "queued_at": "2026-09-01T00:00:00Z",
    }]}), encoding="utf-8")

    result = server.get_competitor_profile("qu", x_api_key=None)
    assert len(result["pending_reviews"]) == 1
    assert result["pending_reviews"][0]["review_id"] == "rev-qu-0001"


def test_resolve_competitor_review_item_flips_status(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    review_queue_path = comp_root / "_portfolio" / "review_queue.json"
    review_queue_path.write_text(json.dumps({"pending_reviews": [{
        "review_id": "rev-qu-0001", "competitor_slug": "qu", "category": None,
        "evidence_id": "eco-sig-1", "kind": "material_signal_review", "reason": "New signal.",
        "current_status": None, "status": "pending", "queued_at": "2026-09-01T00:00:00Z",
    }]}), encoding="utf-8")

    result = server.post_resolve_competitor_review_item("qu", "rev-qu-0001", "reviewed, no change needed", x_api_key=None)
    assert result["ok"] is True

    saved = json.loads(review_queue_path.read_text())
    item = saved["pending_reviews"][0]
    assert item["status"] == "resolved"
    assert item["resolution"] == "reviewed, no change needed"
    # never mutates the underlying battle card
    comp = json.loads((comp_root / "competitors" / "qu" / "competitor.json").read_text())
    assert comp.get("category_battle_cards", {}) == {}


def test_resolve_competitor_review_item_rejects_unknown_review_id(isolated_graph, tmp_path):
    comp_root = tmp_path / "competitor_intelligence"
    _register_competitor(comp_root, "qu", "vendor-qu")
    with pytest.raises(HTTPException) as exc_info:
        server.post_resolve_competitor_review_item("qu", "rev-does-not-exist", "n/a", x_api_key=None)
    assert exc_info.value.status_code == 404


class TestCompetitiveLandscapeExport:
    def test_build_workbook_has_all_four_tabs_with_real_data(self, isolated_graph):
        import sys
        sys.path.insert(0, "system/scripts")
        import competitive_landscape_export as cl_export
        wb = cl_export.build_workbook()
        assert wb.sheetnames == [
            "Market Share by Category", "Battle Cards", "Brand x Category Grid", "Competitor Research Status",
            "RM Battle Cards", "Category Coverage Plan", "Competitor Master", "Vendor Lookup",
            "Evidence Ledger", "Research Queue",
        ]
        grid = wb["Brand x Category Grid"]
        assert grid.max_row == 2  # header + 1 brand
        header = [c.value for c in next(grid.iter_rows(min_row=1, max_row=1))]
        assert header[0] == "brand_name"
        assert "pos_hardware" in header

    def test_competitor_master_backfills_legacy_records_missing_extended_fields(self, isolated_graph, tmp_path):
        """The isolated_graph fixture's own _register_competitor() writes a
        pre-Phase-B competitor.json (no products/strengths/etc keys at
        all) -- exactly the 153-legacy-record shape this must never
        KeyError on."""
        import sys
        sys.path.insert(0, "system/scripts")
        import competitive_landscape_export as cl_export
        comp_root = tmp_path / "competitor_intelligence"
        _register_competitor(comp_root, "qu", "vendor-qu")

        wb = cl_export.build_workbook()
        ws = wb["Competitor Master"]
        headers = [c.value for c in ws[1]]
        for col in ("products", "strengths", "weaknesses", "vulnerabilities", "key_customers", "recent_news", "trends"):
            assert col in headers
        row = dict(zip(headers, [c.value for c in ws[2]]))
        assert row["strengths"] == "(none on file)"
        assert row["trends"] == "(none on file)"

    def test_competitor_master_renders_real_extended_field_content(self, isolated_graph, tmp_path):
        import sys
        sys.path.insert(0, "system/scripts")
        import competitive_landscape_export as cl_export
        import competitor_intelligence_common as cic
        comp_root = tmp_path / "competitor_intelligence"
        _register_competitor(comp_root, "qu", "vendor-qu")
        comp_path = comp_root / "competitors" / "qu" / "competitor.json"
        comp = json.loads(comp_path.read_text())
        comp["strengths"] = [cic.extended_field("Fast checkout flow.", as_of="2026-09-01")]
        comp["vulnerabilities"] = [cic.extended_field("Recent exec churn.", as_of="2026-09-02")]
        comp["trends"] = cic.extended_field("Expanding into franchise back-office.", as_of="2026-09-03")
        comp_path.write_text(json.dumps(comp), encoding="utf-8")

        wb = cl_export.build_workbook()
        ws = wb["Competitor Master"]
        headers = [c.value for c in ws[1]]
        row = dict(zip(headers, [c.value for c in ws[2]]))
        assert "Fast checkout flow." in row["strengths"]
        assert "2026-09-01" in row["strengths"]
        assert "Recent exec churn." in row["vulnerabilities"]
        assert row["trends"] == "Expanding into franchise back-office."


class TestSyncAllTrackedCompetitors:
    """Uses server.compintel directly -- the same already-imported module
    object the isolated_graph fixture's monkeypatch of
    server.compintel_common.ROOT already applies to (competitor_intelligence
    .py's own `import competitor_intelligence_common as cic` binds to the
    identical module object)."""

    def test_syncs_every_registered_competitor(self, isolated_graph, tmp_path):
        comp_root = tmp_path / "competitor_intelligence"
        _register_competitor(comp_root, "qu", "vendor-qu")

        result = server.compintel.sync_all_tracked_competitors()
        assert result["ok"] is True
        assert result["competitors_synced"] == 1
        assert result["results"][0]["competitor_slug"] == "qu"

    def test_never_fails_the_whole_run_on_one_bad_competitor(self, isolated_graph, tmp_path):
        comp_root = tmp_path / "competitor_intelligence"
        reg_path = comp_root / "_portfolio" / "competitor_registry.json"
        reg = json.loads(reg_path.read_text())
        reg["registry"].append({"competitor_slug": "does-not-exist-on-disk", "display_name": "Ghost"})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")

        result = server.compintel.sync_all_tracked_competitors()
        assert result["ok"] is True
        assert result["results"][0]["ok"] is False


class TestVendorListAndCompetitiveLandscapeDownloadRoutes:
    """The rbb_chat.py serving route for the full analysis workbook --
    mirrors test_vendor_list.py's TestVendorListXlsxDownload pattern."""

    def test_competitive_landscape_download_returns_real_xlsx(self, isolated_graph, monkeypatch):
        import io
        import sys
        sys.path.insert(0, str(Path("system/api")))
        import rbb_chat
        from openpyxl import load_workbook

        # Same bypass test_vendor_list.py's sibling TestVendorListXlsxDownload
        # uses -- this route is gated by RBB_CHAT_PASSCODE, a separate secret
        # from server.py's RB_API_KEY, unset in the test environment.
        monkeypatch.setattr(rbb_chat, "_auth_flexible", lambda *a, **k: None)
        resp = rbb_chat.get_competitive_landscape_download(passcode=None, x_chat_passcode=None)
        assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert resp.headers["content-disposition"].endswith('.xlsx"')

        wb = load_workbook(io.BytesIO(resp.body))
        assert wb.sheetnames == [
            "Market Share by Category", "Battle Cards", "Brand x Category Grid", "Competitor Research Status",
            "RM Battle Cards", "Category Coverage Plan", "Competitor Master", "Vendor Lookup",
            "Evidence Ledger", "Research Queue",
        ]

    def test_local_operation_returns_real_download_url(self):
        import sys
        sys.path.insert(0, str(Path("system/api")))
        import rbb_chat
        status, payload = rbb_chat._local_get_competitive_landscape_download_link({})
        assert status == 200
        assert payload["download_url"].startswith("https://rbb-chat.bridgepointops.org/competitive-landscape/download")
