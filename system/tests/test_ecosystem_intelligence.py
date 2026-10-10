import importlib.util
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "system" / "scripts" / "ecosystem_intelligence.py"


spec = importlib.util.spec_from_file_location("ecosystem_intelligence", SCRIPT)
ei = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ei)


def _empty_graph() -> dict:
    return {"sources": [], "entities": [], "relationships": [], "signals": [], "assessments": [], "user_relevance": [], "strategic_recommendations": []}


class NowIsUTCTest(unittest.TestCase):
    """RB-2026-08-28: _now() used to return naive local time -- the one
    inconsistent timestamp source in this codebase, everywhere else uses
    UTC. Confirmed live: during the ~5-6h window each day when local and
    UTC dates diverge (e.g. CDT, UTC-5), a conflict record's detected_at
    (this function) carried yesterday's UTC date while
    intelligence_mutation_engine.build_mutation_brief_block()'s "today"
    filter used real UTC -- a same-day conflict silently excluded from the
    same-day brief's conflict count. Real, reproduced failure:
    test_external_content_mutation.py's two conflict-flagging tests failed
    with conflicts_detected == 0 instead of 1, specifically during this
    window, nothing else about the code path was wrong."""

    def test_now_carries_a_utc_offset(self):
        ts = ei._now()
        # A naive local-time isoformat() string has no offset suffix at
        # all; a UTC-aware one always ends in "+00:00" for timespec="seconds".
        self.assertTrue(ts.endswith("+00:00"), f"_now() = {ts!r} is not UTC-aware")

    def test_now_date_matches_real_utc_date_not_local_date(self):
        from datetime import datetime, timezone
        real_utc_date = datetime.now(timezone.utc).date().isoformat()
        self.assertEqual(ei._now()[:10], real_utc_date)


class EcosystemIntelligenceTest(unittest.TestCase):
    def test_restaurant_brand_entity_normalizes_technomic_like_row(self):
        row = {
            "brand": "Burger King",
            "segment": "QSR Burger",
            "units": "7,200",
            "sales": "$11,000,000,000",
            "auv": "$1,527,778",
            "rank": "3",
        }
        entity = ei.restaurant_brand_entity(row, "src-technomic")
        self.assertEqual(entity["id"], "brand-burger-king")
        self.assertEqual(entity["entity_type"], "brand")
        self.assertEqual(entity["subtype"], "restaurant_brand")
        self.assertEqual(entity["attributes"]["unit_count"], 7200)
        self.assertEqual(entity["attributes"]["system_sales"], 11000000000)
        self.assertEqual(entity["attributes"]["segment"], "QSR Burger")

    def test_restaurant_brand_entity_uses_latest_technomic_year_and_usd(self):
        row = {
            "chain_name": "McDonald's",
            "ignite_id": "823",
            "segment": "LSR",
            "subsegment": "QSR",
            "menu_type": "Burger",
            "rank": "1",
            "2024_u_s_sales_000": "53469000",
            "2024_yoy_sales": "0.0063029203468205914",
            "2024_u_s_units": "13557",
            "2024_yoy_units": "0.0074310767630229617",
            "2024_auv_000": "3960",
            "2023_u_s_sales_000": "53134100",
            "2023_u_s_units": "13457",
        }
        entity = ei.restaurant_brand_entity(row, "src-technomic")
        attrs = entity["attributes"]
        self.assertEqual(attrs["technomic_latest_year"], 2024)
        self.assertEqual(attrs["system_sales"], 53469000000)
        self.assertEqual(attrs["auv"], 3960000)
        self.assertEqual(attrs["unit_count"], 13557)
        self.assertAlmostEqual(attrs["sales_delta"], 0.6302920346820591)
        self.assertEqual(attrs["technomic_history"]["2023"]["system_sales_usd"], 53134100000)

    def test_vendor_relationship_creates_queryable_pos_edge(self):
        graph = _empty_graph()
        row = {
            "brand": "Burger King",
            "vendor": "PAR Technology",
            "category": "POS",
            "product": "Brink POS",
            "deployment_status": "active",
            "rollout_stage": "active rollout",
            "stores_deployed": "3000",
            "penetration_pct": "42%",
            "risk": "yellow",
            "confidence": "high",
            "source": "PAR announcement",
            "source_url": "https://example.com/par",
        }
        rel = ei.vendor_relationship(row, graph)
        self.assertEqual(rel["from_entity_id"], "brand-burger-king")
        self.assertEqual(rel["to_entity_id"], "vendor-par-technology")
        self.assertEqual(rel["category"], "pos")
        self.assertEqual(rel["deployment"]["stage"], "active_rollout")
        self.assertEqual(rel["deployment"]["deployed_units"], 3000)
        self.assertEqual(rel["deployment"]["penetration_pct"], 42)
        self.assertEqual(rel["risk"], "yellow")
        # No source_type supplied → defaults to unsourced_spreadsheet → provisional
        self.assertEqual(rel["evidence_posture"], "provisional")
        self.assertIn("not proof", rel["interpretation_scope"])
        self.assertEqual(len(graph["entities"]), 2)
        self.assertEqual(len(graph["sources"]), 1)

    def test_source_quality_model_sets_defaults_from_source_type(self):
        """Source type should auto-derive quality, evidence_posture, and confidence."""
        graph = _empty_graph()
        row_primary = {
            "brand": "Chipotle", "vendor": "Oracle", "category": "pos",
            "product": "Oracle MICROS", "source": "Chipotle 10-K 2024",
            "source_type": "primary_operator_statement",
        }
        rel = ei.vendor_relationship(row_primary, graph)
        self.assertEqual(rel["evidence_posture"], "substantiated")
        self.assertEqual(rel["confidence"]["level"], "high")
        src = graph["sources"][0]
        self.assertEqual(src["quality"], "primary")
        self.assertEqual(src["source_type"], "primary_operator_statement")

        graph2 = _empty_graph()
        row_logo = {
            "brand": "Chipotle", "vendor": "NCR", "category": "pos",
            "source": "NCR website logo",
            "source_type": "vendor_logo_customer_page",
        }
        rel2 = ei.vendor_relationship(row_logo, graph2)
        self.assertEqual(rel2["evidence_posture"], "provisional")
        self.assertEqual(rel2["confidence"]["level"], "low")
        self.assertEqual(graph2["sources"][0]["quality"], "weak")

    def test_pos_scope_distinction_system_of_record_vs_hardware(self):
        """NCR (hardware) and NewPOS (system-of-record) must produce separate edges."""
        graph = _empty_graph()
        row_sor = {
            "brand": "McDonald's", "vendor": "NewPOS", "category": "pos",
            "product": "NewPOS", "pos_role": "system_of_record_pos",
            "source": "McDonald's tech blog", "source_type": "primary_operator_statement",
        }
        row_hw = {
            "brand": "McDonald's", "vendor": "NCR", "category": "pos",
            "product": "NCR hardware", "pos_role": "approved_hardware_vendor",
            "source": "Trade report", "source_type": "credible_trade_reporting",
        }
        rel_sor = ei.vendor_relationship(row_sor, graph)
        rel_hw = ei.vendor_relationship(row_hw, graph)
        self.assertNotEqual(rel_sor["id"], rel_hw["id"],
                            "Edges with different pos_role must have distinct IDs")
        self.assertEqual(rel_sor["vendor_role"], "system_of_record_pos")
        self.assertEqual(rel_hw["vendor_role"], "approved_hardware_vendor")
        self.assertEqual(rel_sor["deployment"]["scope"], "system_of_record_pos")
        self.assertEqual(rel_hw["deployment"]["scope"], "approved_hardware_vendor")

    def test_vendor_edges_allow_geography_channel_and_service_multiplicity(self):
        """Same brand/category can carry multiple hardware/service ecosystem edges."""
        graph = _empty_graph()
        rows = [
            {
                "brand": "McDonald's", "vendor": "HP", "category": "pos",
                "product": "HP hardware", "pos_role": "approved_hardware_vendor",
                "source": "Operator correction", "source_type": "operator_context",
                "geography": "international", "channel": "approved_hardware",
            },
            {
                "brand": "McDonald's", "vendor": "HP", "category": "pos",
                "product": "HP hardware", "pos_role": "approved_hardware_vendor",
                "source": "Domestic contradiction check", "source_type": "operator_context",
                "geography": "domestic", "channel": "approved_hardware",
                "evidence_posture": "refuted",
            },
            {
                "brand": "McDonald's", "vendor": "MAPS", "category": "pos",
                "product": "NCR and PAR equipment resale / installation / service",
                "pos_role": "hardware_reseller_service_provider",
                "source": "Operator correction", "source_type": "operator_context",
                "geography": "domestic", "channel": "reseller_service",
                "service_role": "reseller_installation_service",
            },
        ]
        rels = [ei.vendor_relationship(row, graph) for row in rows]
        self.assertEqual(len({rel["id"] for rel in rels}), 3)
        self.assertEqual(rels[0]["geography"], "international")
        self.assertEqual(rels[1]["geography"], "domestic")
        self.assertEqual(rels[2]["vendor_role"], "hardware_reseller_service_provider")
        self.assertEqual(rels[2]["service_role"], "reseller_installation_service")

    def test_logo_and_case_study_do_not_become_systemwide_deployment(self):
        """A logo/case study is scoped evidence, not a systemwide brand deployment."""
        graph = _empty_graph()
        logo_row = {
            "brand": "McDonald's", "vendor": "Voosh", "category": "back_office",
            "product": "Voosh", "source": "Voosh customer logo page",
            "source_type": "vendor_logo_customer_page",
        }
        case_row = {
            "brand": "McDonald's", "vendor": "Voosh", "category": "back_office",
            "product": "Voosh", "source": "Voosh McDonald's operator case study",
            "source_type": "case_study", "customer_operator": "40-store franchisee operator",
            "scope_unit_count": "40",
        }
        rel_logo = ei.vendor_relationship(logo_row, graph)
        rel_case = ei.vendor_relationship(case_row, graph)
        self.assertNotEqual(rel_logo["id"], rel_case["id"])
        self.assertEqual(rel_logo["deployment_claim_type"], "logo_or_customer_page")
        self.assertEqual(rel_logo["evidence_posture"], "provisional")
        self.assertEqual(rel_case["deployment_claim_type"], "limited_operator_deployment")
        self.assertEqual(rel_case["customer_operator"], "40-store franchisee operator")
        self.assertEqual(rel_case["scope_unit_count"], 40.0)
        self.assertNotEqual(rel_case["deployment_claim_type"], "systemwide_deployment")

    def test_conflicting_vendor_claims_stored_as_separate_edges(self):
        """Two sources disagree on POS vendor — both edges must be preserved."""
        graph = _empty_graph()
        row_a = {
            "brand": "Wendy's", "vendor": "PAR Technology", "category": "pos",
            "product": "Brink POS", "pos_role": "system_of_record_pos",
            "source": "PAR earnings call", "source_type": "primary_vendor_announcement",
            "evidence_posture": "partially_substantiated",
        }
        row_b = {
            "brand": "Wendy's", "vendor": "NCR", "category": "pos",
            "product": "Aloha POS", "pos_role": "legacy_incumbent",
            "source": "Trade report 2021", "source_type": "credible_trade_reporting",
            "evidence_posture": "partially_substantiated",
        }
        rel_a = ei.vendor_relationship(row_a, graph)
        rel_b = ei.vendor_relationship(row_b, graph)
        ei._upsert_relationship(graph, rel_a)
        ei._upsert_relationship(graph, rel_b)
        self.assertEqual(len(graph["relationships"]), 2,
                         "Both conflicting vendor relationships must be stored")
        roles = {r["vendor_role"] for r in graph["relationships"]}
        self.assertIn("system_of_record_pos", roles)
        self.assertIn("legacy_incumbent", roles)

    def test_query_brand_returns_full_profile(self):
        """query_brand should surface entity + related vendor relationships."""
        import io, contextlib
        graph = _empty_graph()
        graph["entities"].append({
            "id": "brand-starbucks", "name": "Starbucks", "entity_type": "brand",
            "subtype": "restaurant_brand", "status": "active", "domains": ["restaurants"],
            "aliases": [], "attributes": {"segment": "LSR", "unit_count": 16000},
            "sources": ["src-technomic"], "confidence": {"level": "high"},
            "notes": "", "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        })
        graph["relationships"].append({
            "id": "rel-brand-starbucks-pos-system-of-record-pos-vendor-oracle",
            "from_entity_id": "brand-starbucks", "to_entity_id": "vendor-oracle",
            "relationship_type": "uses_vendor_for_category", "status": "active",
            "domains": ["restaurants"], "category": "pos", "vendor_role": "system_of_record_pos",
            "product": "Oracle MICROS", "deployment": {"stage": "full_deployment", "scope": "system_of_record_pos", "evidence": "Starbucks 10-K"},
            "evidence_posture": "substantiated", "interpretation_scope": "System of record POS.",
            "risk": "green", "sources": ["src-starbucks-10k"],
            "confidence": {"level": "high", "rationale": "Primary operator statement"},
            "strategic_note": "Entrenched; low replacement risk.",
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        })
        # Monkeypatch _read_graph so query_brand uses our test graph.
        original_read = ei._read_graph
        ei._read_graph = lambda *a, **kw: graph
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                class FakeArgs:
                    brand = "starbucks"
                ei.query_brand(FakeArgs())
            result = json.loads(buf.getvalue())
        finally:
            ei._read_graph = original_read
        self.assertEqual(result["entity"]["name"], "Starbucks")
        self.assertEqual(result["vendor_relationship_count"], 1)
        self.assertEqual(result["vendor_relationships"][0]["vendor_role"], "system_of_record_pos")

    def test_load_csv_like_normalizes_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brands.csv"
            path.write_text("Brand Name,Unit Count\nTest Brand,12\n", encoding="utf-8")
            rows = ei.load_rows(path)
        self.assertEqual(rows, [{"brand_name": "Test Brand", "unit_count": "12"}])

    def test_detailed_rows_merge_company_franchise_and_international_metrics(self):
        graph = {"entities": [], "sources": [], "relationships": []}
        row = {
            "chain_name": "McDonald's",
            "ignite_id": "823",
            "segment": "LSR",
            "subsegment": "QSR",
            "menu_type": "Burger",
            "year": "2024",
            "u_s_sales_000": "53469000",
            "u_s_units": "13557",
            "auv_000": "3960",
            "u_s_company_units": "671",
            "u_s_franchise_units": "12886",
            "u_s_company_sales_000": "3197000",
            "u_s_franchise_sales_000": "50272000",
            "international_units": "29920",
            "international_sales_000": "77246000",
        }
        stats = ei.merge_restaurant_detail_rows(graph, [row], "src-technomic")
        attrs = graph["entities"][0]["attributes"]
        detail = attrs["technomic_detailed_history"]["2024"]
        self.assertEqual(stats["detail_rows_updated"], 1)
        self.assertEqual(detail["company_units"], 671)
        self.assertEqual(detail["franchise_units"], 12886)
        self.assertEqual(detail["international_units"], 29920)
        self.assertEqual(detail["international_sales_usd"], 77246000000)


    def test_ingest_vendors_dry_run_counts_correctly(self):
        """Dry-run ingest of the vendor template should report correct row/added counts."""
        import io, contextlib
        template = ROOT / "system" / "inbox" / "ecosystem" / "vendor_evidence_template.csv"
        if not template.exists():
            self.skipTest("vendor_evidence_template.csv not found")
        rows = ei.load_rows(template)
        self.assertGreater(len(rows), 0, "Template must have at least one data row")
        graph = _empty_graph()
        added = 0
        for row in rows:
            rel = ei.vendor_relationship(row, graph)
            if rel and ei._upsert_relationship(graph, rel):
                added += 1
        self.assertEqual(added, len([r for r in rows if ei._first(r, ei.BRAND_NAME_KEYS)
                                     and ei._first(r, ei.VENDOR_KEYS)
                                     and ei._norm_category(ei._first(r, ei.CATEGORY_KEYS))]))

    def test_graph_validates_after_vendor_ingest(self):
        """Graph must still validate after vendor relationships are written."""
        import subprocess
        result = subprocess.run(
            ["python3", str(ROOT / "system" / "schemas" / "validate.py"), "--ecosystem-only"],
            capture_output=True, text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, f"Validation failed:\n{result.stderr or result.stdout}")

    def test_promote_posture_advances_ladder(self):
        """promote_posture should move posture forward and reject backward moves."""
        graph = _empty_graph()
        rel = {
            "id": "rel-brand-test-pos-unknown-vendor-test",
            "from_entity_id": "brand-test", "to_entity_id": "vendor-test",
            "relationship_type": "uses_vendor_for_category", "status": "active",
            "domains": ["restaurants"], "category": "pos", "vendor_role": "unknown",
            "product": None, "deployment": {"stage": "unknown", "scope": "unknown", "evidence": "test"},
            "evidence_posture": "provisional",
            "interpretation_scope": "test", "risk": "unknown", "sources": ["src-test"],
            "confidence": {"level": "low"}, "strategic_note": "",
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        }
        graph["relationships"].append(rel)

        original_read = ei._read_graph
        original_write = ei._write_graph
        original_blue_sheet_common = ei._blue_sheet_common
        written = {}

        def fake_write(g, *, dry_run=False):
            written["graph"] = g

        ei._read_graph = lambda *a, **kw: graph
        ei._write_graph = fake_write
        # RB-2026-08-23: promote_posture now calls the Blue Sheet coverage
        # hook -- without this, this unrelated test writes real entries
        # into blue_sheets/_portfolio/coverage_log.jsonl on every run.
        ei._blue_sheet_common = None

        import io, contextlib
        buf = io.StringIO()

        class FakeArgs:
            relationship_id = rel["id"]
            new_posture = "partially_substantiated"
            source_title = "Trade magazine confirmation"
            source_type = "credible_trade_reporting"
            source_url = None
            dry_run = False

        try:
            with contextlib.redirect_stdout(buf):
                rc = ei.promote_posture(FakeArgs())
        finally:
            ei._read_graph = original_read
            ei._write_graph = original_write
            ei._blue_sheet_common = original_blue_sheet_common

        self.assertEqual(rc, 0)
        result = json.loads(buf.getvalue())
        self.assertEqual(result["from_posture"], "provisional")
        self.assertEqual(result["to_posture"], "partially_substantiated")
        # Verify the write captured the new posture
        updated_rel = next(r for r in written["graph"]["relationships"] if r["id"] == rel["id"])
        self.assertEqual(updated_rel["evidence_posture"], "partially_substantiated")

    def test_promote_posture_rejects_backward_move(self):
        """Cannot demote posture via promote-posture."""
        graph = _empty_graph()
        rel = {
            "id": "rel-brand-test2-pos-unknown-vendor-test2",
            "from_entity_id": "brand-test2", "to_entity_id": "vendor-test2",
            "relationship_type": "uses_vendor_for_category", "status": "active",
            "domains": ["restaurants"], "category": "pos", "vendor_role": "unknown",
            "product": None, "deployment": {"stage": "unknown", "scope": "unknown", "evidence": "test"},
            "evidence_posture": "substantiated",
            "interpretation_scope": "test", "risk": "unknown", "sources": ["src-test"],
            "confidence": {"level": "high"}, "strategic_note": "",
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        }
        graph["relationships"].append(rel)

        original_read = ei._read_graph
        ei._read_graph = lambda *a, **kw: graph

        import io, contextlib
        buf = io.StringIO()

        class FakeArgs:
            relationship_id = rel["id"]
            new_posture = "provisional"
            source_title = "Some source"
            source_type = "credible_trade_reporting"
            source_url = None
            dry_run = False

        try:
            with contextlib.redirect_stdout(buf):
                rc = ei.promote_posture(FakeArgs())
        finally:
            ei._read_graph = original_read

        self.assertEqual(rc, 1)
        result = json.loads(buf.getvalue())
        self.assertIn("error", result)

    def test_query_vendor_returns_required_fields(self):
        """query-vendor output must contain all fields specified in the sprint brief."""
        import io, contextlib
        graph = _empty_graph()
        graph["entities"].append({
            "id": "brand-chipotle", "name": "Chipotle", "entity_type": "brand",
            "subtype": "restaurant_brand", "status": "active", "domains": ["restaurants"],
            "aliases": [], "attributes": {"segment": "LSR", "unit_count": 3600, "system_sales": 11000000000, "auv": 3000000},
            "sources": ["src-technomic"], "confidence": {"level": "high"},
            "notes": "", "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        })
        graph["entities"].append({
            "id": "vendor-oracle", "name": "Oracle", "entity_type": "vendor",
            "subtype": "restaurant_technology_vendor", "status": "active", "domains": ["restaurants"],
            "aliases": [], "attributes": {}, "sources": [], "confidence": {"level": "medium"},
            "notes": "", "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        })
        graph["relationships"].append({
            "id": "rel-brand-chipotle-pos-system-of-record-pos-vendor-oracle",
            "from_entity_id": "brand-chipotle", "to_entity_id": "vendor-oracle",
            "relationship_type": "uses_vendor_for_category", "status": "active",
            "domains": ["restaurants"], "category": "pos", "vendor_role": "system_of_record_pos",
            "product": "Oracle MICROS",
            "deployment": {"stage": "full_deployment", "scope": "system_of_record_pos",
                           "deployed_units": 3600, "penetration_pct": 100, "evidence": "Trade report"},
            "evidence_posture": "partially_substantiated", "interpretation_scope": "System of record POS.",
            "risk": "green", "sources": ["src-oracle-ref"],
            "confidence": {"level": "medium"}, "strategic_note": "Stable Oracle deployment.",
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        })

        original_read = ei._read_graph
        ei._read_graph = lambda *a, **kw: graph

        buf = io.StringIO()

        class FakeArgs:
            vendor = "oracle"
            category = "pos"

        try:
            with contextlib.redirect_stdout(buf):
                ei.query_vendor(FakeArgs())
        finally:
            ei._read_graph = original_read

        result = json.loads(buf.getvalue())
        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        required_fields = [
            "brand", "segment", "unit_count", "sales", "auv",
            "vendor_category", "vendor_role", "product",
            "deployment_status", "deployment_scope", "penetration_pct",
            "geography", "channel", "service_role",
            "deployment_claim_type", "customer_operator", "scope_unit_count",
            "risk", "evidence_posture", "interpretation_scope",
            "confidence", "sources", "relationship_coverage", "strategic_implication",
        ]
        for field in required_fields:
            self.assertIn(field, item, f"Missing required field in query-vendor output: {field}")


class RelationshipConflictDetectionTest(unittest.TestCase):
    """RB Research Intelligence Engine Phase 1 (2026-07-20): a brand+category
    must never silently hold two unresolved live claims from different
    vendors. See ecosystem_intelligence.check_relationship_conflict()."""

    def _rel(self, vendor_id: str, status: str, updated_at: str = "2026-01-01T00:00:00Z",
             *, score=None) -> dict:
        rel = {
            "id": f"rel-brand-x-pos-{vendor_id}",
            "from_entity_id": "brand-x",
            "to_entity_id": vendor_id,
            "category": "pos",
            "status": status,
            "updated_at": updated_at,
        }
        if score is not None:
            rel["confidence"] = {"level": "high" if score >= 0.85 else "medium", "score": score,
                                  "rationale": "", "review_after": None}
        return rel

    def test_no_existing_relationship_is_not_a_conflict(self):
        graph = _empty_graph()
        verdict = ei.check_relationship_conflict(graph, self._rel("vendor-a", "active"))
        self.assertFalse(verdict["conflict"])

    def test_same_vendor_updating_itself_is_not_a_conflict(self):
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-a", "evaluating"))
        verdict = ei.check_relationship_conflict(graph, self._rel("vendor-a", "active"))
        self.assertFalse(verdict["conflict"])

    def test_two_tentative_claims_for_same_category_coexist(self):
        """Multiple vendors can legitimately be 'evaluating' the same open category."""
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-a", "evaluating"))
        verdict = ei.check_relationship_conflict(graph, self._rel("vendor-b", "evaluating"))
        self.assertFalse(verdict["conflict"])

    def test_confirmed_selection_auto_supersedes_weaker_prior_claim(self):
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-a", "evaluating"))
        verdict = ei.check_relationship_conflict(graph, self._rel("vendor-b", "active"))
        self.assertTrue(verdict["conflict"])
        self.assertEqual(verdict["resolution"], "auto_superseded")
        self.assertEqual(verdict["existing"]["to_entity_id"], "vendor-a")

    def test_two_confirmed_claims_with_equal_confidence_are_recorded_alongside(self):
        """2026-09-25 (Confidence-Based Auto-Recording): two claims that are
        both status="active" and carry equal (here: both defaulted, since
        _rel() sets no explicit confidence) confidence can't be told apart --
        the incoming one is recorded, not silently dropped, but doesn't
        overwrite the incumbent. No more "requires_confirmation" -- this
        never blocks."""
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-a", "active"))
        incoming = self._rel("vendor-b", "active")
        verdict = ei.check_relationship_conflict(graph, incoming)
        self.assertTrue(verdict["conflict"])
        self.assertEqual(verdict["resolution"], "recorded_alongside")
        self.assertEqual(incoming["status"], "rumored")
        self.assertEqual(incoming["related_claim_id"], verdict["existing"]["id"])

    def test_weaker_incoming_claim_against_confirmed_incumbent_is_recorded_not_promoted(self):
        """The Worldpay/PAR case: a stale 'evaluating' claim arriving after an
        incumbent is already confirmed must not be silently promoted to the
        current answer -- but (2026-09-25) it's still recorded, not blocked."""
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-a", "active"))
        incoming = self._rel("vendor-b", "evaluating")
        verdict = ei.check_relationship_conflict(graph, incoming)
        self.assertTrue(verdict["conflict"])
        self.assertEqual(verdict["resolution"], "recorded_alongside")
        self.assertEqual(incoming["related_claim_id"], verdict["existing"]["id"])
        # Status stays "evaluating" here (its own self-declared status, not
        # forcibly relabeled "rumored") -- only a same-tier "active vs
        # active" loser gets downgraded to "rumored".
        self.assertEqual(incoming["status"], "evaluating")


class ConfidenceScoreConflictResolutionTest(unittest.TestCase):
    """Confidence-Based Auto-Recording (2026-09-25): within the same status
    tier (both "active"), confidence.score is what decides auto_superseded
    vs recorded_alongside -- the McDonald's/Genius/NEWPOS scenario Todd
    described directly. See check_relationship_conflict()'s "both claims are
    active" branch."""

    def _rel(self, vendor_id: str, score: float, updated_at: str = "2026-01-01T00:00:00Z") -> dict:
        return {
            "id": f"rel-brand-x-pos-{vendor_id}",
            "from_entity_id": "brand-x",
            "to_entity_id": vendor_id,
            "category": "pos",
            "status": "active",
            "updated_at": updated_at,
            "confidence": {"level": "high" if score >= 0.85 else "medium", "score": score,
                            "rationale": "", "review_after": None},
        }

    def test_weak_trade_press_claim_does_not_supersede_strong_incumbent(self):
        """National Restaurant News reports Genius won McDonald's POS
        (confidence ~0.5 per confidence_calibration's "medium" trade-press
        band) against an existing confirmed NEWPOS claim at 0.85 -- must not
        overwrite; must still be recorded."""
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-newpos", 0.85))
        incoming = self._rel("vendor-genius", 0.5)
        verdict = ei.check_relationship_conflict(graph, incoming)
        self.assertEqual(verdict["resolution"], "recorded_alongside")
        self.assertEqual(incoming["status"], "rumored")
        newpos = graph["relationships"][0]
        self.assertEqual(newpos["status"], "active")

    def test_official_confirmation_supersedes_high_confidence_incumbent(self):
        """McDonald's/Global Payments directly confirms Genius for POS
        (confidence 0.95, confidence_calibration's official/primary band)
        against an existing 0.85 claim -- must supersede even though the
        raw gap (0.10) is under the standard 0.15 margin, because 0.95 is
        squarely top-tier confidence."""
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-newpos", 0.85))
        incoming = self._rel("vendor-genius", 0.95)
        verdict = ei.check_relationship_conflict(graph, incoming)
        self.assertEqual(verdict["resolution"], "auto_superseded")
        self.assertEqual(incoming["status"], "active")
        newpos = graph["relationships"][0]
        self.assertEqual(newpos["status"], "active")  # check_relationship_conflict doesn't mutate; resolve_and_upsert_relationship does

    def test_meaningful_margin_supersedes_without_hitting_high_threshold(self):
        """A 0.7 claim against a 0.5 incumbent clears the +0.15 margin
        without either score being individually "high" -- must still
        supersede on the margin alone."""
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-a", 0.5))
        incoming = self._rel("vendor-b", 0.7)
        verdict = ei.check_relationship_conflict(graph, incoming)
        self.assertEqual(verdict["resolution"], "auto_superseded")

    def test_exact_tie_at_high_confidence_does_not_supersede(self):
        """Two genuinely top-tier claims (both 0.95) -- neither should
        silently overwrite the other; recorded alongside instead."""
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-a", 0.95))
        incoming = self._rel("vendor-b", 0.95)
        verdict = ei.check_relationship_conflict(graph, incoming)
        self.assertEqual(verdict["resolution"], "recorded_alongside")
        self.assertEqual(incoming["status"], "rumored")

    def test_resolve_and_upsert_full_flow_end_to_end(self):
        """The full McDonald's/Genius/NEWPOS scenario through resolve_and_
        upsert_relationship(): NEWPOS marked superseded (kept, not deleted),
        Genius becomes the new active claim."""
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-x", "name": "Brand X"},
            {"id": "vendor-newpos", "name": "NEWPOS"},
            {"id": "vendor-genius", "name": "Genius"},
        ])
        graph["relationships"].append(self._rel("vendor-newpos", 0.85))
        incoming = self._rel("vendor-genius", 0.95)

        with tempfile.TemporaryDirectory() as tmp:
            original_system_dir = ei.core.SYSTEM_DIR
            ei.core.SYSTEM_DIR = Path(tmp)
            try:
                outcome = ei.resolve_and_upsert_relationship(graph, incoming)
            finally:
                ei.core.SYSTEM_DIR = original_system_dir

        self.assertEqual(outcome["conflict"]["resolution"], "auto_superseded")
        newpos = next(r for r in graph["relationships"] if r["to_entity_id"] == "vendor-newpos")
        genius = next(r for r in graph["relationships"] if r["to_entity_id"] == "vendor-genius")
        self.assertEqual(newpos["status"], "superseded")
        self.assertEqual(newpos["superseded_by"], genius["id"])
        self.assertEqual(genius["status"], "active")
        # NEWPOS is kept in the graph, never deleted.
        self.assertIn(newpos, graph["relationships"])


class ConfidenceBackfillIntegrationTest(unittest.TestCase):
    """confidence.score, once genuinely absent, is derived on the fly by
    _relationship_confidence_score() rather than defaulting both sides to
    an indistinguishable score -- 2026-09-25."""

    def test_missing_score_derived_from_level_not_treated_as_zero(self):
        rel_high = {"confidence": {"level": "high", "score": None}}
        rel_low = {"confidence": {"level": "low", "score": None}}
        self.assertGreater(ei._relationship_confidence_score(rel_high), ei._relationship_confidence_score(rel_low))

    def test_bare_string_confidence_still_derives_a_real_score(self):
        rel = {"confidence": "high"}
        self.assertEqual(ei._relationship_confidence_score(rel), 0.85)

    def test_missing_confidence_entirely_defaults_to_medium_band(self):
        rel = {}
        self.assertEqual(ei._relationship_confidence_score(rel), 0.5)


class RelationshipConflictDetectionTestContinued(unittest.TestCase):
    """Continuation of RelationshipConflictDetectionTest above -- split into
    its own class only because the new ConfidenceScoreConflictResolution
    Test/ConfidenceBackfillIntegrationTest classes were inserted between
    them; same _rel() fixture helper, unchanged from the original."""

    def _rel(self, vendor_id: str, status: str, updated_at: str = "2026-01-01T00:00:00Z",
             *, score=None) -> dict:
        rel = {
            "id": f"rel-brand-x-pos-{vendor_id}",
            "from_entity_id": "brand-x",
            "to_entity_id": vendor_id,
            "category": "pos",
            "status": status,
            "updated_at": updated_at,
        }
        if score is not None:
            rel["confidence"] = {"level": "high" if score >= 0.85 else "medium", "score": score,
                                  "rationale": "", "review_after": None}
        return rel

    def test_historical_and_superseded_rivals_are_ignored(self):
        graph = _empty_graph()
        graph["relationships"].append(self._rel("vendor-a", "historical"))
        graph["relationships"].append(self._rel("vendor-b", "superseded"))
        verdict = ei.check_relationship_conflict(graph, self._rel("vendor-c", "active"))
        self.assertFalse(verdict["conflict"])

    def test_resolve_and_upsert_auto_supersede_writes_conflict_record_and_marks_existing(self):
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-x", "name": "Brand X"},
            {"id": "vendor-a", "name": "Vendor A"},
            {"id": "vendor-b", "name": "Vendor B"},
        ])
        existing = self._rel("vendor-a", "evaluating")
        graph["relationships"].append(existing)
        incoming = self._rel("vendor-b", "active")

        with tempfile.TemporaryDirectory() as tmp:
            original_system_dir = ei.core.SYSTEM_DIR
            original_queue_path = ei.core.CONFLICT_QUEUE_PATH
            ei.core.SYSTEM_DIR = Path(tmp)
            queue_path = Path(tmp) / "inbox" / "ecosystem" / "conflict_queue.jsonl"
            ei.core.CONFLICT_QUEUE_PATH = queue_path
            try:
                outcome = ei.resolve_and_upsert_relationship(graph, incoming)
                self.assertTrue(queue_path.exists())
                record = json.loads(queue_path.read_text().splitlines()[0])
            finally:
                ei.core.SYSTEM_DIR = original_system_dir
                ei.core.CONFLICT_QUEUE_PATH = original_queue_path

        self.assertTrue(outcome["added"])
        self.assertEqual(outcome["conflict"]["resolution"], "auto_superseded")
        self.assertEqual(existing["status"], "superseded")
        self.assertEqual(incoming["status"], "active")
        self.assertEqual(record["resolution"], "auto_superseded")
        self.assertEqual(record["existing"]["vendor_name"], "Vendor A")
        self.assertEqual(record["incoming"]["vendor_name"], "Vendor B")

    def test_resolve_and_upsert_conflict_records_incoming_without_blocking(self):
        """2026-09-25 (Confidence-Based Auto-Recording): equal-confidence
        rival "active" claims no longer produce a blocking "conflicting"/
        requires_confirmation state -- the incoming claim is still written
        (never silently dropped), downgraded to "rumored" and cross-
        referenced to the incumbent it didn't beat."""
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-x", "name": "Brand X"},
            {"id": "vendor-a", "name": "Vendor A"},
            {"id": "vendor-b", "name": "Vendor B"},
        ])
        graph["relationships"].append(self._rel("vendor-a", "active"))
        incoming = self._rel("vendor-b", "active")

        with tempfile.TemporaryDirectory() as tmp:
            original_system_dir = ei.core.SYSTEM_DIR
            ei.core.SYSTEM_DIR = Path(tmp)
            try:
                outcome = ei.resolve_and_upsert_relationship(graph, incoming)
            finally:
                ei.core.SYSTEM_DIR = original_system_dir

        self.assertTrue(outcome["added"])
        self.assertEqual(outcome["conflict"]["resolution"], "recorded_alongside")
        self.assertEqual(incoming["status"], "rumored")
        self.assertEqual(incoming["related_claim_id"], "rel-brand-x-pos-vendor-a")
        self.assertNotIn("requires_confirmation", incoming)
        self.assertNotIn("conflicts_with", incoming)

    def test_query_brand_surfaces_open_conflicts(self):
        import io, contextlib
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-x", "name": "Brand X", "domains": ["restaurants"]},
            {"id": "vendor-a", "name": "Vendor A"},
            {"id": "vendor-b", "name": "Vendor B"},
        ])
        graph["relationships"].append(self._rel("vendor-a", "active"))
        conflicting = self._rel("vendor-b", "conflicting")
        conflicting["conflicts_with"] = "rel-brand-x-pos-vendor-a"
        conflicting["requires_confirmation"] = True
        graph["relationships"].append(conflicting)

        original_read = ei._read_graph
        ei._read_graph = lambda *a, **kw: graph
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                class FakeArgs:
                    brand = "brand x"
                ei.query_brand(FakeArgs())
            result = json.loads(buf.getvalue())
        finally:
            ei._read_graph = original_read

        self.assertTrue(result["has_unresolved_conflicts"])
        self.assertEqual(len(result["open_conflicts"]), 1)
        conflict = result["open_conflicts"][0]
        self.assertEqual(conflict["incumbent_vendor"], "Vendor A")
        self.assertEqual(conflict["contested_vendor"], "Vendor B")
        self.assertIn("CONFLICT DETECTED", conflict["note"])


class MergeBrandEntitiesTest(unittest.TestCase):
    """RB Vendor-First Baseline Project, Phase 2 (2026-08-03).

    _resolve_brand_entity_id()'s token-subset rule only catches a *shorter*
    incoming brand name matching a *longer* existing entity, never the
    reverse -- so a Phase 2 evidence record naming a brand with extra
    context baked in (e.g. "Applebee's (Dine Brands Global)") silently
    creates a brand-new duplicate entity instead of resolving to the
    existing plain "Applebee's" one. Confirmed live, Drive-Thru/Voice AI
    category batch: 6 of 39 rows hit entity_resolution_required because a
    later clean row (plain "Carl's Jr.") then collided ambiguously between
    the real "Carl's Jr." entity and an earlier compound duplicate via the
    same rule running in reverse. merge-brand-entities (_merge_brand_entities_
    in_graph) exists to clean these up. Uses the pure, I/O-free core function
    directly -- never call merge_brand_entities(args) itself in a test, since
    it calls _read_graph() with no override and that function's default
    argument is bound to the real ecosystem_intelligence.json path at module
    import time (the same class of hazard documented in this module's "RB
    Vendor-First Baseline Project" comment block and in
    system/CLAUDE_HANDOFF_RB_UNIFIED_RESTAURANT_TECH_GRAPH_2026-08-01.md's
    "Incident during Phase 5" writeup)."""

    def test_merge_reassigns_relationship_with_no_existing_collision(self):
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-applebees", "name": "Applebee's", "aliases": []},
            {"id": "brand-applebees-dine-brands-global", "name": "Applebee's (Dine Brands Global)", "aliases": []},
        ])
        graph["relationships"].append({
            "id": "rel-brand-applebees-dine-brands-global-pos-vendor-toast",
            "from_entity_id": "brand-applebees-dine-brands-global",
            "to_entity_id": "vendor-toast",
            "category": "pos",
            "status": "active",
            "sources": ["src-a"],
            "source_assertions": [],
        })

        report = ei._merge_brand_entities_in_graph(
            graph, canonical_id="brand-applebees", duplicate_ids=["brand-applebees-dine-brands-global"],
        )

        self.assertEqual(report["relationships_reassigned"], 1)
        self.assertEqual(report["relationships_merged"], 0)
        self.assertEqual(len(graph["relationships"]), 1)
        rel = graph["relationships"][0]
        self.assertEqual(rel["from_entity_id"], "brand-applebees")
        self.assertEqual(rel["id"], "rel-brand-applebees-pos-vendor-toast")
        self.assertEqual([e["id"] for e in graph["entities"]], ["brand-applebees"])
        self.assertIn("Applebee's (Dine Brands Global)", graph["entities"][0]["aliases"])

    def test_merge_combines_source_assertions_when_canonical_already_has_same_edge(self):
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-chipotle-mexican-grill", "name": "Chipotle Mexican Grill", "aliases": []},
            {"id": "brand-chipotle", "name": "Chipotle", "aliases": []},
        ])
        graph["relationships"].extend([
            {
                "id": "rel-brand-chipotle-mexican-grill-loyalty-vendor-sessionm",
                "from_entity_id": "brand-chipotle-mexican-grill",
                "to_entity_id": "vendor-sessionm",
                "category": "loyalty",
                "status": "active",
                "sources": ["src-existing"],
                "source_assertions": [{"source_id": "src-existing", "discovered_at": "2026-01-01", "posture": "current"}],
            },
            {
                "id": "rel-brand-chipotle-loyalty-vendor-sessionm",
                "from_entity_id": "brand-chipotle",
                "to_entity_id": "vendor-sessionm",
                "category": "loyalty",
                "status": "historical",
                "sources": ["src-new"],
                "source_assertions": [{"source_id": "src-new", "discovered_at": "2026-08-03", "posture": "historical"}],
            },
        ])

        report = ei._merge_brand_entities_in_graph(
            graph, canonical_id="brand-chipotle-mexican-grill", duplicate_ids=["brand-chipotle"],
        )

        self.assertEqual(report["relationships_reassigned"], 0)
        self.assertEqual(report["relationships_merged"], 1)
        self.assertEqual(len(graph["relationships"]), 1)
        rel = graph["relationships"][0]
        self.assertEqual(rel["from_entity_id"], "brand-chipotle-mexican-grill")
        self.assertEqual({a["source_id"] for a in rel["source_assertions"]}, {"src-existing", "src-new"})
        self.assertEqual(set(rel["sources"]), {"src-existing", "src-new"})

    def test_merge_unknown_canonical_returns_error_without_mutating_graph(self):
        graph = _empty_graph()
        graph["entities"].append({"id": "brand-x", "name": "Brand X", "aliases": []})
        before = json.dumps(graph, sort_keys=True)

        report = ei._merge_brand_entities_in_graph(graph, canonical_id="brand-does-not-exist", duplicate_ids=["brand-x"])

        self.assertIn("error", report)
        self.assertEqual(json.dumps(graph, sort_keys=True), before)

    def test_merge_unknown_duplicate_id_is_reported_not_raised(self):
        graph = _empty_graph()
        graph["entities"].append({"id": "brand-x", "name": "Brand X", "aliases": []})
        report = ei._merge_brand_entities_in_graph(graph, canonical_id="brand-x", duplicate_ids=["brand-ghost"])
        self.assertEqual(report["duplicates"], [{"id": "brand-ghost", "status": "not_found"}])

    def test_merge_reassigns_to_entity_id_for_a_vendor_side_duplicate(self):
        """The PAR Technology case (2026-08-04): two vendor entities for the
        same real company ('PAR Technology' and 'PAR Technology (Brink
        POS)') made check_relationship_conflict() see two *rival* vendors
        both claiming Papa Johns' POS -- a false conflict, not a real one.
        Merging must reassign to_entity_id, not just from_entity_id."""
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "vendor-par-technology", "name": "PAR Technology", "aliases": []},
            {"id": "vendor-par-technology-brink-pos", "name": "PAR Technology (Brink POS)", "aliases": []},
        ])
        graph["relationships"].append({
            "id": "rel-brand-papa-johns-pos-vendor-par-technology-brink-pos",
            "from_entity_id": "brand-papa-johns",
            "to_entity_id": "vendor-par-technology-brink-pos",
            "category": "pos",
            "status": "active",
            "sources": ["src-a"],
            "source_assertions": [],
        })

        report = ei._merge_brand_entities_in_graph(
            graph, canonical_id="vendor-par-technology", duplicate_ids=["vendor-par-technology-brink-pos"],
        )

        self.assertEqual(report["relationships_reassigned"], 1)
        rel = graph["relationships"][0]
        self.assertEqual(rel["to_entity_id"], "vendor-par-technology")
        self.assertEqual(rel["from_entity_id"], "brand-papa-johns")  # untouched
        self.assertEqual(rel["id"], "rel-brand-papa-johns-pos-vendor-par-technology")
        self.assertEqual([e["id"] for e in graph["entities"]], ["vendor-par-technology"])

        # And the false conflict is gone: a second, rival relationship for
        # the same brand+category now correctly sees only one live vendor.
        verdict = ei.check_relationship_conflict(graph, {
            "from_entity_id": "brand-papa-johns", "to_entity_id": "vendor-par-technology",
            "category": "pos", "status": "active", "vendor_role": None,
        })
        self.assertFalse(verdict["conflict"])


class BackfillDeploymentClaimTypeTest(unittest.TestCase):
    """RB Vendor-First Baseline Project (2026-08-05). See
    _backfill_deployment_claim_types_in_graph()'s docstring: reconcile_
    workbook_row() only started setting deployment_claim_type in the same
    fix this backfill complements -- every relationship written before that
    (365 of 381 in the real graph) had none, which makes classify_
    relationship() in relationship_classification.py silently skip it."""

    def test_backfills_from_vendor_role_and_deployment_status(self):
        graph = _empty_graph()
        graph["relationships"].append({
            "id": "rel-brand-x-pos-vendor-y", "from_entity_id": "brand-x", "to_entity_id": "vendor-y",
            "category": "pos", "status": "active", "vendor_role": "franchisee_deployment",
            "deployment_status": "franchisee_deployment_not_brand_standard", "source_assertions": [],
        })
        report = ei._backfill_deployment_claim_types_in_graph(graph)
        self.assertEqual(report["updated"], 1)
        self.assertEqual(graph["relationships"][0]["deployment_claim_type"], "franchisee_deployment")

    def test_does_not_overwrite_existing_value_without_force(self):
        graph = _empty_graph()
        graph["relationships"].append({
            "id": "rel-brand-x-pos-vendor-y", "from_entity_id": "brand-x", "to_entity_id": "vendor-y",
            "category": "pos", "status": "active", "vendor_role": "pilot",
            "deployment_status": "pilot_only", "deployment_claim_type": "systemwide_deployment",
            "source_assertions": [],
        })
        report = ei._backfill_deployment_claim_types_in_graph(graph)
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["skipped_already_set"], 1)
        self.assertEqual(graph["relationships"][0]["deployment_claim_type"], "systemwide_deployment")

    def test_force_recomputes_existing_value(self):
        graph = _empty_graph()
        graph["relationships"].append({
            "id": "rel-brand-x-pos-vendor-y", "from_entity_id": "brand-x", "to_entity_id": "vendor-y",
            "category": "pos", "status": "active", "vendor_role": "pilot",
            "deployment_status": "pilot_only", "deployment_claim_type": "systemwide_deployment",
            "source_assertions": [],
        })
        report = ei._backfill_deployment_claim_types_in_graph(graph, force=True)
        self.assertEqual(report["updated"], 1)
        self.assertEqual(graph["relationships"][0]["deployment_claim_type"], "pilot")

    def test_relationship_with_no_signal_at_all_is_skipped_not_forced_to_unknown(self):
        graph = _empty_graph()
        graph["relationships"].append({
            "id": "rel-brand-x-pos-vendor-y", "from_entity_id": "brand-x", "to_entity_id": "vendor-y",
            "category": "pos", "status": "active", "source_assertions": [],
        })
        report = ei._backfill_deployment_claim_types_in_graph(graph)
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["skipped_no_signal"], 1)
        self.assertNotIn("deployment_claim_type", graph["relationships"][0])


class ResolveEntityIdAnyTypeTest(unittest.TestCase):
    """RB-2026-09-08, 3-store unification Phase 3. Unlike
    _resolve_brand_entity_id(), this resolves across BOTH brand and vendor
    entity types -- built to canonicalize real, external per-entity caches
    (entity_alerts_cache.json, technomic_watchlist_promoted.json,
    market_signals_earnings.jsonl) that mix both kinds of name under one
    raw string. Exact name/alias match only, never guesses."""

    def _entity(self, entity_id, name, entity_type, aliases=None):
        return {"id": entity_id, "name": name, "entity_type": entity_type,
                "aliases": aliases or [], "attributes": {}, "sources": [],
                "confidence": {}, "domains": ["restaurants"]}

    def test_resolves_a_brand_by_exact_name(self):
        graph = _empty_graph()
        graph["entities"].append(self._entity("brand-mcdonald-s", "McDonald's", "brand"))
        self.assertEqual(ei._resolve_entity_id_any_type("McDonald's", graph), "brand-mcdonald-s")

    def test_resolves_a_vendor_by_exact_name(self):
        graph = _empty_graph()
        graph["entities"].append(self._entity("vendor-toast", "Toast", "vendor"))
        self.assertEqual(ei._resolve_entity_id_any_type("Toast", graph), "vendor-toast")

    def test_resolves_by_alias(self):
        graph = _empty_graph()
        graph["entities"].append(self._entity("vendor-ncr", "NCR", "vendor", aliases=["NCR Corporation", "NCR Aloha"]))
        self.assertEqual(ei._resolve_entity_id_any_type("NCR Corporation", graph), "vendor-ncr")

    def test_returns_none_for_unknown_name(self):
        graph = _empty_graph()
        graph["entities"].append(self._entity("brand-mcdonald-s", "McDonald's", "brand"))
        self.assertIsNone(ei._resolve_entity_id_any_type("Totally Fake Company XYZ", graph))

    def test_returns_none_rather_than_guess_on_genuine_ambiguity(self):
        """Confirmed live against real data (2026-09-08): "NCR Voyix" is
        genuinely ambiguous in the real graph -- vendor-ncr (name "NCR",
        alias "NCR Voyix") and vendor-ncr-voyix (name "NCR Voyix" directly)
        both match. This is the exact real-world case this function must
        never silently resolve one way or the other."""
        graph = _empty_graph()
        graph["entities"].append(self._entity("vendor-ncr", "NCR", "vendor", aliases=["NCR Voyix"]))
        graph["entities"].append(self._entity("vendor-ncr-voyix", "NCR Voyix", "vendor"))
        self.assertIsNone(ei._resolve_entity_id_any_type("NCR Voyix", graph))

    def test_empty_name_returns_none(self):
        graph = _empty_graph()
        self.assertIsNone(ei._resolve_entity_id_any_type("", graph))


class NormalizeCategoriesTest(unittest.TestCase):
    """RB Vendor-First Baseline Project (2026-08-05). `category` is free
    text in the schema -- every Phase 2 research batch used its own
    free-text label per record rather than one fixed string per category,
    fragmenting 39 distinct values in the graph where ~18 canonical ones
    were intended. See _normalize_categories_in_graph()'s docstring."""

    def _rel(self, brand_id, vendor_id, category, **overrides):
        rel = {
            "id": f"rel-{brand_id}-{ei._slug(category)}-unknown-{vendor_id}",
            "from_entity_id": brand_id, "to_entity_id": vendor_id,
            "category": category, "vendor_role": None, "status": "active",
            "sources": ["src-a"], "source_assertions": [{"source_id": "src-a", "discovered_at": "2026-08-05", "posture": "current"}],
        }
        rel.update(overrides)
        return rel

    def test_renames_and_regenerates_id_when_no_collision(self):
        graph = _empty_graph()
        graph["relationships"].append(self._rel("brand-x", "vendor-y", "kds"))
        report = ei._normalize_categories_in_graph(graph)
        self.assertEqual(report["changed"], 1)
        self.assertEqual(report["merged_into_existing"], 0)
        rel = graph["relationships"][0]
        self.assertEqual(rel["category"], "kds_kitchen_ops")
        # ids always use _slug()'s hyphen convention, even though the
        # category *field* itself is stored underscore-separated -- matches
        # how reconcile_workbook_row() has always built ids.
        self.assertEqual(rel["id"], "rel-brand-x-kds-kitchen-ops-unknown-vendor-y")

    def test_merges_into_existing_relationship_when_categories_collapse_onto_same_edge(self):
        graph = _empty_graph()
        graph["relationships"].extend([
            self._rel("brand-x", "vendor-y", "kds_kitchen_ops", sources=["src-existing"],
                       source_assertions=[{"source_id": "src-existing", "discovered_at": "2026-01-01", "posture": "current"}]),
            self._rel("brand-x", "vendor-y", "kds", sources=["src-new"],
                       source_assertions=[{"source_id": "src-new", "discovered_at": "2026-08-05", "posture": "current"}]),
        ])
        report = ei._normalize_categories_in_graph(graph)
        self.assertEqual(report["changed"], 0)
        self.assertEqual(report["merged_into_existing"], 1)
        self.assertEqual(len(graph["relationships"]), 1)
        rel = graph["relationships"][0]
        self.assertEqual(rel["category"], "kds_kitchen_ops")
        self.assertEqual({a["source_id"] for a in rel["source_assertions"]}, {"src-existing", "src-new"})

    def test_already_canonical_category_is_left_untouched(self):
        graph = _empty_graph()
        graph["relationships"].append(self._rel("brand-x", "vendor-y", "pos"))
        report = ei._normalize_categories_in_graph(graph)
        self.assertEqual(report["changed"], 0)
        self.assertEqual(report["unchanged"], 1)
        self.assertEqual(graph["relationships"][0]["category"], "pos")

    def test_short_category_slug_does_not_corrupt_unrelated_id_substring(self):
        """Regression guard: an early implementation string-replaced the old
        category slug directly inside the existing id, which could corrupt
        the id if that short slug also occurred inside the brand/vendor id
        portions (e.g. "kds" appearing inside some other token). The fixed
        version rebuilds the id from the relationship's own field values
        instead, so this can't happen."""
        graph = _empty_graph()
        graph["relationships"].append(self._rel("brand-kdsystems-diner", "vendor-y", "kds"))
        report = ei._normalize_categories_in_graph(graph)
        self.assertEqual(report["changed"], 1)
        rel = graph["relationships"][0]
        self.assertEqual(rel["from_entity_id"], "brand-kdsystems-diner")  # untouched
        self.assertEqual(rel["id"], "rel-brand-kdsystems-diner-kds-kitchen-ops-unknown-vendor-y")


class ResearchRequestPipelineTest(unittest.TestCase):
    """RB Research Intelligence Engine Phase 2 (2026-07-20): staleness and
    unresolved conflicts must become persistent, prioritized research
    requests instead of passive flags that reset every brief cycle."""

    def _rel(self, **overrides) -> dict:
        rel = {
            "id": "rel-brand-x-pos-vendor-a",
            "from_entity_id": "brand-x",
            "to_entity_id": "vendor-a",
            "category": "pos",
            "status": "active",
            "evidence_posture": "provisional",
            "confidence": {"level": "low", "review_after": None},
        }
        rel.update(overrides)
        return rel

    def _graph(self, rel: dict) -> dict:
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-x", "name": "Brand X"},
            {"id": "vendor-a", "name": "Vendor A"},
        ])
        graph["relationships"].append(rel)
        return graph

    def test_staleness_flag_is_explicitly_cleared_when_no_longer_overdue(self):
        """Latent gap fix: a relationship re-verified into the future (or with
        no review_after at all) must not keep a stale True carried in the dict."""
        rel = self._rel(staleness_flag=True, confidence={"level": "low", "review_after": None})
        graph = self._graph(rel)
        ei._check_staleness_on_graph(graph, date.today())
        self.assertFalse(graph["relationships"][0]["staleness_flag"])

        future = (date.today() + timedelta(days=30)).isoformat()
        rel2 = self._rel(staleness_flag=True, confidence={"level": "low", "review_after": future})
        graph2 = self._graph(rel2)
        ei._check_staleness_on_graph(graph2, date.today())
        self.assertFalse(graph2["relationships"][0]["staleness_flag"])

    def test_generate_research_requests_staleness_trigger_priority_tiers(self):
        cases = [(5, "low"), (45, "medium"), (120, "high")]
        for days_overdue, expected_priority in cases:
            review_after = (date.today() - timedelta(days=days_overdue)).isoformat()
            rel = self._rel(
                staleness_flag=True,
                confidence={"level": "low", "review_after": review_after},
            )
            graph = self._graph(rel)
            requests = ei.generate_research_requests(graph)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["trigger"], "staleness")
            self.assertEqual(requests[0]["priority"], expected_priority,
                              f"days_overdue={days_overdue}")
            self.assertIn("Vendor A", requests[0]["question"])
            self.assertIn("Brand X", requests[0]["question"])

    def test_generate_research_requests_conflict_trigger_is_high_priority(self):
        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-x", "name": "Brand X"},
            {"id": "vendor-a", "name": "Vendor A"},
            {"id": "vendor-b", "name": "Vendor B"},
        ])
        incumbent = self._rel(id="rel-incumbent", to_entity_id="vendor-a", status="active")
        contested = self._rel(
            id="rel-contested", to_entity_id="vendor-b", status="conflicting",
            conflicts_with="rel-incumbent",
        )
        graph["relationships"].extend([incumbent, contested])

        requests = ei.generate_research_requests(graph)
        conflict_requests = [r for r in requests if r["trigger"] == "conflict"]
        self.assertEqual(len(conflict_requests), 1)
        self.assertEqual(conflict_requests[0]["priority"], "high")
        self.assertIn("Vendor A", conflict_requests[0]["question"])
        self.assertIn("Vendor B", conflict_requests[0]["question"])

    def test_update_research_requests_persists_and_auto_resolves(self):
        review_after = (date.today() - timedelta(days=10)).isoformat()
        rel = self._rel(staleness_flag=True, confidence={"level": "low", "review_after": review_after})
        graph = self._graph(rel)

        with tempfile.TemporaryDirectory() as tmp:
            original_system_dir = ei.core.SYSTEM_DIR
            ei.core.SYSTEM_DIR = Path(tmp)
            try:
                first = ei.update_research_requests(graph)
                self.assertEqual(first["opened"], 1)
                self.assertEqual(first["still_open"], 1)
                first_detected_at = first["open_requests"][0]["detected_at"]

                # Re-running with the same still-stale condition must not
                # duplicate or reset the detection timestamp.
                second = ei.update_research_requests(graph)
                self.assertEqual(second["opened"], 0)
                self.assertEqual(second["still_open"], 1)
                self.assertEqual(second["open_requests"][0]["detected_at"], first_detected_at)

                # Condition clears (relationship re-verified) — request must auto-resolve.
                graph["relationships"][0]["staleness_flag"] = False
                third = ei.update_research_requests(graph)
                self.assertEqual(third["resolved"], 1)
                self.assertEqual(third["still_open"], 0)

                store = json.loads(
                    (Path(tmp) / "inbox" / "ecosystem" / "research_requests.json").read_text()
                )
                self.assertEqual(store["requests"][0]["status"], "resolved")
            finally:
                ei.core.SYSTEM_DIR = original_system_dir

    def test_query_brand_surfaces_open_research_requests(self):
        import io, contextlib
        graph = _empty_graph()
        graph["entities"].append({"id": "brand-x", "name": "Brand X", "domains": ["restaurants"]})

        with tempfile.TemporaryDirectory() as tmp:
            original_system_dir = ei.core.SYSTEM_DIR
            ei.core.SYSTEM_DIR = Path(tmp)
            rr_path = Path(tmp) / "inbox" / "ecosystem" / "research_requests.json"
            rr_path.parent.mkdir(parents=True, exist_ok=True)
            rr_path.write_text(json.dumps({"requests": [
                {"id": "rr-1", "entity_id": "brand-x", "status": "open", "question": "Is X still true?"},
                {"id": "rr-2", "entity_id": "brand-x", "status": "resolved", "question": "Old question"},
                {"id": "rr-3", "entity_id": "brand-other", "status": "open", "question": "Unrelated"},
            ]}))

            original_read = ei._read_graph
            ei._read_graph = lambda *a, **kw: graph
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    class FakeArgs:
                        brand = "brand x"
                    ei.query_brand(FakeArgs())
                result = json.loads(buf.getvalue())
            finally:
                ei._read_graph = original_read
                ei.core.SYSTEM_DIR = original_system_dir

        self.assertEqual(len(result["open_research_requests"]), 1)
        self.assertEqual(result["open_research_requests"][0]["id"], "rr-1")

    def test_generate_weak_evidence_requests_reads_open_items_only(self):
        """vendor_verification_queue.json (linkedin_freshness_bridge.py) was a
        write-only queue nothing read — found while building this pipeline.
        Its open items must fold into the same research-request shape."""
        with tempfile.TemporaryDirectory() as tmp:
            original_system_dir = ei.core.SYSTEM_DIR
            ei.core.SYSTEM_DIR = Path(tmp)
            queue_path = Path(tmp) / "inbox" / "ecosystem" / "vendor_verification_queue.json"
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            queue_path.write_text(json.dumps({"items": [
                {
                    "task_id": "verify-rel-brand-blaze-pizza-pos-vendor-qu",
                    "status": "open",
                    "relationship_id": "rel-brand-blaze-pizza-pos-vendor-qu",
                    "vendor": "Qu",
                    "customer_brand": "Blaze Pizza",
                    "category": "pos",
                    "weak_signal_classification": "vendor_claimed",
                    "recommended_checks": ["Find operator/vendor primary statement or case study."],
                },
                {
                    "task_id": "verify-rel-already-done",
                    "status": "resolved",
                    "vendor": "Toast",
                    "customer_brand": "Some Other Brand",
                    "category": "pos",
                },
            ]}))
            try:
                requests = ei.generate_weak_evidence_requests()
            finally:
                ei.core.SYSTEM_DIR = original_system_dir

        self.assertEqual(len(requests), 1)
        req = requests[0]
        self.assertEqual(req["trigger"], "weak_evidence")
        self.assertEqual(req["entity_id"], "brand-blaze-pizza")
        self.assertEqual(req["vendor_id"], "vendor-qu")
        self.assertEqual(req["priority"], "medium")
        self.assertIn("Qu", req["question"])
        self.assertIn("Blaze Pizza", req["question"])

    def test_update_research_requests_includes_weak_evidence(self):
        graph = _empty_graph()
        with tempfile.TemporaryDirectory() as tmp:
            original_system_dir = ei.core.SYSTEM_DIR
            ei.core.SYSTEM_DIR = Path(tmp)
            queue_path = Path(tmp) / "inbox" / "ecosystem" / "vendor_verification_queue.json"
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            queue_path.write_text(json.dumps({"items": [{
                "task_id": "verify-1",
                "status": "open",
                "vendor": "Qu",
                "customer_brand": "Blaze Pizza",
                "category": "pos",
            }]}))
            try:
                result = ei.update_research_requests(graph)
            finally:
                ei.core.SYSTEM_DIR = original_system_dir

        self.assertEqual(result["opened"], 1)
        self.assertEqual(result["open_requests"][0]["trigger"], "weak_evidence")


SCHEMA_PATH = ROOT / "system" / "schemas" / "ecosystem_intelligence.schema.json"
GRAPH_PATH = ROOT / "system" / "ecosystem_intelligence.json"


class SchemaExtensionTest(unittest.TestCase):
    """Unified Restaurant-Tech Graph request (2026-07-31) Phase 1: adds
    ai_application, deployment_status, deployment_detail, and
    source_assertions[] to the relationship definition. All four are
    optional additions -- this must not require any data migration for
    the 62 existing relationships."""

    @classmethod
    def setUpClass(cls):
        from jsonschema import Draft7Validator
        cls.schema = json.loads(SCHEMA_PATH.read_text())
        cls.validator = Draft7Validator(cls.schema)

    def _minimal_graph(self, relationship: dict) -> dict:
        return {
            "version": 2,
            "contract": "rb_ecosystem_intelligence_v1",
            "last_updated": "2026-07-31",
            "domain_packs": ["restaurants"],
            "entities": [],
            "relationships": [relationship],
            "signals": [],
            "assessments": [],
            "sources": [],
            "user_relevance": [],
            "strategic_recommendations": [],
        }

    def _base_relationship(self, **overrides) -> dict:
        rel = {
            "id": "rel-brand-test-brand-pos-vendor-test-vendor",
            "from_entity_id": "brand-test-brand",
            "to_entity_id": "vendor-test-vendor",
            "relationship_type": "uses_vendor_for_category",
            "status": "active",
            "sources": ["src-test"],
            "confidence": {"level": "high"},
            "created_at": "2026-07-31T00:00:00",
            "updated_at": "2026-07-31T00:00:00",
        }
        rel.update(overrides)
        return rel

    def test_new_fields_are_all_valid_on_a_relationship(self):
        rel = self._base_relationship(
            ai_application="voice_ai",
            deployment_status="significant_deployed_footprint",
            deployment_detail="300+ locations per 2026 SEC filing",
            source_assertions=[{
                "source_id": "src-2026-sec-10k",
                "url": "https://www.sec.gov/example",
                "title": "2026 Form 10-K",
                "publisher": "Example Corp",
                "published_at": "2026-03-01",
                "discovered_at": "2026-07-31",
                "source_type": "sec_filing",
                "source_authority": "sec_or_regulatory_filing",
                "commercial_incentive_posture": "operator_self_interested",
                "claim_type": "named_customer_win",
                "contracted_locations": None,
                "live_locations": 300,
                "rollout_target": None,
                "customer_explicitly_named": True,
                "evidence_origin": "operator",
                "paraphrase": "Filing states 300+ deployed locations.",
                "posture": "current",
            }],
        )
        errors = list(self.validator.iter_errors(self._minimal_graph(rel)))
        self.assertEqual(errors, [], f"Unexpected validation errors: {errors}")

    def test_new_fields_are_optional_relationship_without_them_still_valid(self):
        rel = self._base_relationship()
        errors = list(self.validator.iter_errors(self._minimal_graph(rel)))
        self.assertEqual(errors, [])

    def test_invalid_ai_application_value_rejected(self):
        rel = self._base_relationship(ai_application="not_a_real_application")
        errors = list(self.validator.iter_errors(self._minimal_graph(rel)))
        self.assertTrue(errors)

    def test_invalid_deployment_status_value_rejected(self):
        rel = self._base_relationship(deployment_status="not_a_real_status")
        errors = list(self.validator.iter_errors(self._minimal_graph(rel)))
        self.assertTrue(errors)

    def test_source_assertion_missing_required_field_rejected(self):
        rel = self._base_relationship(source_assertions=[{
            "source_id": "src-x",
            # missing url/discovered_at/posture -- url is nullable so omit
            # discovered_at and posture, both required.
        }])
        errors = list(self.validator.iter_errors(self._minimal_graph(rel)))
        self.assertTrue(errors)

    def test_source_assertion_posture_enum_enforced(self):
        rel = self._base_relationship(source_assertions=[{
            "source_id": "src-x",
            "url": None,
            "discovered_at": "2026-07-31",
            "posture": "not_a_real_posture",
        }])
        errors = list(self.validator.iter_errors(self._minimal_graph(rel)))
        self.assertTrue(errors)

    def test_real_ecosystem_graph_still_validates_after_schema_extension(self):
        """The unmigrated, real 62-relationship graph must still validate
        against the extended schema with zero changes to its data."""
        graph = json.loads(GRAPH_PATH.read_text())
        errors = list(self.validator.iter_errors(graph))
        self.assertEqual(errors, [], f"Real graph no longer validates: {errors[:3]}")

    def test_conflict_detection_output_fields_are_schema_valid(self):
        """check_relationship_conflict()/resolve_and_upsert_relationship() write
        status='conflicting'/'superseded' plus requires_confirmation/
        conflicts_with/superseded_by (see resolve_and_upsert_relationship's
        auto_superseded and requires_confirmation branches). These were unit
        tested at the dict-shape level (test_resolve_and_upsert_conflict_marks_
        incoming_conflicting etc.) but never round-tripped through the actual
        schema -- the schema didn't allow any of them until this fix, so the
        very first real requires_confirmation conflict to reach an actual
        --confirm write (Phase 2 vendor-evidence import, 2026-08-03: Jack in
        the Box and Shake Shack both already had an active Oracle POS claim
        when new Qu evidence came in) failed _write_graph()'s post-write
        validation. Guard against that regression directly."""
        conflicting_rel = self._base_relationship(
            status="conflicting",
            requires_confirmation=True,
            conflicts_with="rel-brand-test-brand-pos-vendor-rival-vendor",
        )
        errors = list(self.validator.iter_errors(self._minimal_graph(conflicting_rel)))
        self.assertEqual(errors, [], f"Unexpected validation errors: {errors}")

        superseded_rel = self._base_relationship(
            status="superseded",
            superseded_by="rel-brand-test-brand-pos-vendor-new-vendor",
        )
        errors = list(self.validator.iter_errors(self._minimal_graph(superseded_rel)))
        self.assertEqual(errors, [], f"Unexpected validation errors: {errors}")

    def test_invalid_status_value_still_rejected(self):
        rel = self._base_relationship(status="not_a_real_status")
        errors = list(self.validator.iter_errors(self._minimal_graph(rel)))
        self.assertTrue(errors)


class ClearEntityFieldTest(unittest.TestCase):
    """Data Tier Architecture work (2026-09-30): no existing public
    function could edit or clear an arbitrary entity-level scalar field
    -- clear_entity_field() closes that gap. Real motivating case: a
    stray private sales-opportunity note in ecosystem_intelligence.json's
    entities[].notes (a Tier 1 "canonical public" field), which must
    never carry Tier 2 (per-account) content. Same isolation pattern as
    test_entity_dedup_review.py: direct assignment onto ei.core.
    ECOSYSTEM_INTELLIGENCE_PATH, restored in tearDown."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._graph_path = Path(self._tmpdir.name) / "ecosystem_intelligence.json"
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        self._tmpdir.cleanup()

    def _write_graph(self, entities) -> None:
        graph = _empty_graph()
        graph["entities"] = entities
        graph["contract"] = "rb_ecosystem_intelligence_v1"
        graph["version"] = 1
        graph["last_updated"] = "2026-09-30"
        graph["domain_packs"] = ["restaurants"]
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _brand(self, entity_id, **extra):
        e = {
            "id": entity_id, "name": "Test Brand", "entity_type": "brand", "subtype": "restaurant_brand",
            "status": "active", "aliases": [], "attributes": {}, "sources": [],
            "confidence": {"level": "high"}, "domains": ["restaurants"],
        }
        e.update(extra)
        return e

    def test_clears_an_allowlisted_field(self):
        self._write_graph([self._brand("brand-test", notes="Private opportunity note")])
        result = ei.clear_entity_field("brand-test", "notes", reason="test cleanup")
        self.assertTrue(result)
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-test")
        self.assertNotIn("notes", entity)

    def test_rejects_a_field_not_on_the_allowlist(self):
        self._write_graph([self._brand("brand-test", name="Real Name")])
        with self.assertRaises(ValueError):
            ei.clear_entity_field("brand-test", "name", reason="should never be allowed")

    def test_no_op_for_unknown_entity(self):
        self._write_graph([self._brand("brand-test", notes="Some note")])
        result = ei.clear_entity_field("brand-does-not-exist", "notes", reason="test")
        self.assertFalse(result)
        # Real entity's note must be untouched.
        graph = json.loads(self._graph_path.read_text())
        entity = next(e for e in graph["entities"] if e["id"] == "brand-test")
        self.assertEqual(entity["notes"], "Some note")

    def test_no_op_when_field_already_absent(self):
        self._write_graph([self._brand("brand-test")])  # no "notes" key at all
        result = ei.clear_entity_field("brand-test", "notes", reason="test")
        self.assertFalse(result)

    def test_clearing_deletes_the_key_never_writes_null(self):
        """Real 2026-09-30 bug caught live: the schema requires `notes` be
        a string when present (no null allowed) -- an earlier version of
        this function set the field to None instead of deleting it,
        which wrote invalid content to the real production file before
        _write_graph()'s post-write validation caught it. Regression
        guard: the key must be deleted, never nulled."""
        self._write_graph([self._brand("brand-test", notes="Private note")])
        ei.clear_entity_field("brand-test", "notes", reason="test")
        raw = json.loads(self._graph_path.read_text())
        entity = next(e for e in raw["entities"] if e["id"] == "brand-test")
        self.assertNotIn("notes", entity)  # not entity.get("notes") is None


class UpsertEntityAttributePrecedenceTest(unittest.TestCase):
    """RB-2026-10-01 (Todd): confirmed live on vendor-par-ops -- an
    unattended spreadsheet/"vendor evidence row" ingestion (ecosystem_
    intelligence.py's vendor_relationship()) silently flipped its
    operator-provided attributes.primary_category from
    "back_office_operations" to "pos" overnight, because _upsert_entity's
    attribute merge used to unconditionally overwrite any existing
    attribute with whatever the latest upsert supplied, with no
    precedence check at all. Regression guard: an attribute that already
    has a value must never be silently overwritten by a later upsert;
    only an attribute that isn't yet set gets filled in."""

    def test_existing_attribute_value_is_not_overwritten(self):
        graph = {"entities": []}
        graph["entities"].append({
            "id": "vendor-par-ops", "name": "PAR Ops", "entity_type": "vendor",
            "attributes": {"primary_category": "back_office_operations"},
            "confidence": {"level": "medium", "rationale": "Operator-provided."},
        })
        changed = ei._upsert_entity(graph, {
            "id": "vendor-par-ops", "name": "PAR Ops", "entity_type": "vendor",
            "attributes": {"primary_category": "pos"},
            "confidence": {"level": "medium", "rationale": "Observed in vendor evidence row."},
        })
        self.assertFalse(changed)  # merge path, not a new entity
        entity = next(e for e in graph["entities"] if e["id"] == "vendor-par-ops")
        self.assertEqual(entity["attributes"]["primary_category"], "back_office_operations")

    def test_unset_attribute_is_still_filled_in(self):
        graph = {"entities": []}
        graph["entities"].append({
            "id": "vendor-new", "name": "New Vendor", "entity_type": "vendor",
            "attributes": {},
        })
        ei._upsert_entity(graph, {
            "id": "vendor-new", "name": "New Vendor", "entity_type": "vendor",
            "attributes": {"primary_category": "pos"},
        })
        entity = next(e for e in graph["entities"] if e["id"] == "vendor-new")
        self.assertEqual(entity["attributes"]["primary_category"], "pos")

    def test_a_different_unset_key_in_the_same_call_is_still_filled_in(self):
        graph = {"entities": []}
        graph["entities"].append({
            "id": "vendor-par-ops", "name": "PAR Ops", "entity_type": "vendor",
            "attributes": {"primary_category": "back_office_operations"},
        })
        ei._upsert_entity(graph, {
            "id": "vendor-par-ops", "name": "PAR Ops", "entity_type": "vendor",
            "attributes": {"primary_category": "pos", "new_field": "new_value"},
        })
        entity = next(e for e in graph["entities"] if e["id"] == "vendor-par-ops")
        self.assertEqual(entity["attributes"]["primary_category"], "back_office_operations")
        self.assertEqual(entity["attributes"]["new_field"], "new_value")


if __name__ == "__main__":
    unittest.main()
