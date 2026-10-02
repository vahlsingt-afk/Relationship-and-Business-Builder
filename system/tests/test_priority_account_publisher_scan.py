"""
test_priority_account_publisher_scan.py — RB-DEFECT-069, 2026-09-10.

Coverage for priority_account_publisher_scan.py, the review-first scanner
built to close a real gap: RestaurantNews.com (and other trade publishers)
being "monitored" only ever meant "feeds the general news cap," not "every
article about a specific priority account is captured." See the module's
own docstring and defects/RB-DEFECT-069_priority-account-publisher-coverage-
gap_2026-09-10.md for full context.

Isolated throughout (own tmp graph, own tmp customers_prospects/ and
master_account_plans/ trees, own tmp candidate store, injected fetcher --
never a real network call). Same isolation discipline as
test_tech_stack_relationship_promotion.py.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"
MP_ENGINE_DIR = ROOT / "master_account_plans" / "_engine"

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(MP_ENGINE_DIR))

import ecosystem_intelligence as ei  # noqa: E402
import ecosystem_brief as eb  # noqa: E402
import entity_alerts as ea  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import mp_common  # noqa: E402
import mp_impact_review  # noqa: E402
import priority_account_publisher_scan as pas  # noqa: E402


def _graph_with(entities=None, sources=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-10",
        "entities": entities or [], "relationships": [], "signals": [],
        "sources": sources or [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _entity(entity_id, name, entity_type="brand", aliases=None):
    return {"id": entity_id, "name": name, "entity_type": entity_type, "aliases": aliases or [],
            "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}


DEL_TACO = _entity("brand-del-taco", "Del Taco", aliases=["Del Taco Restaurants"])
BLAZE = _entity("brand-blaze-pizza", "Blaze Pizza")
TOAST = _entity("vendor-toast", "Toast", entity_type="vendor")

MATERIAL_HEADLINE = "Del Taco Selects Toast for New Payments Platform Rollout"


def _rss_bytes(items: list[dict]) -> bytes:
    """Minimal valid RSS 2.0 body matching web_scanner.parse_rss_items()'s
    real expected tags (title/link/description/pubDate)."""
    body = ["<rss><channel>"]
    for it in items:
        body.append(
            "<item>"
            f"<title>{it['title']}</title>"
            f"<link>{it['url']}</link>"
            f"<description>{it.get('description', '')}</description>"
            f"<pubDate>{it['pub_date']}</pubDate>"
            "</item>"
        )
    body.append("</channel></rss>")
    return "".join(body).encode("utf-8")


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)

        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._store_path = tmp / "priority_account_publisher_candidates.json"

        cpc_root = tmp / "customers_prospects"
        (cpc_root / "_portfolio").mkdir(parents=True)
        mp_root = tmp / "master_account_plans"
        (mp_root / "_portfolio").mkdir(parents=True)

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_store_path = pas.STORE_PATH
        self._orig_cpc_root = cpc.ROOT
        self._orig_mp_root = mp_common.ROOT

        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        pas.STORE_PATH = self._store_path
        cpc.ROOT = cpc_root
        mp_common.ROOT = mp_root

        self._cpc_root = cpc_root
        self._mp_root = mp_root

        (cpc_root / "_portfolio" / "customers_prospects_registry.json").write_text(
            json.dumps({"registry": []}), encoding="utf-8",
        )
        (mp_root / "_portfolio" / "master_account_plan_registry.json").write_text(
            json.dumps({"registry": []}), encoding="utf-8",
        )

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        pas.STORE_PATH = self._orig_store_path
        cpc.ROOT = self._orig_cpc_root
        mp_common.ROOT = self._orig_mp_root
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _all_candidates(self) -> list[dict]:
        """Confidence-Based Auto-Recording Phase 5 (2026-09-25): scan() now
        auto-confirms a qualifying candidate immediately, so pending_
        candidates() is empty right after a scan -- tests inspecting what a
        scan just produced read the full candidate store instead."""
        return list(pas._load_store()["candidates"].values())

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _add_customers_prospects_account(self, slug: str, display_name: str, *, status: str = "active") -> None:
        acct_dir = self._cpc_root / "accounts" / slug
        acct_dir.mkdir(parents=True)
        (acct_dir / "account.json").write_text(json.dumps({"display_name": display_name}), encoding="utf-8")
        (acct_dir / "evidence.jsonl").write_text("", encoding="utf-8")
        reg_path = self._cpc_root / "_portfolio" / "customers_prospects_registry.json"
        reg = json.loads(reg_path.read_text())
        reg["registry"].append({"account_id": f"acct-{slug}", "status": status, "aliases": []})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")

    def _add_mp_vendor_row(self, vendor_slug: str, rows: list[dict]) -> None:
        vendor_dir = self._mp_root / "vendors" / vendor_slug
        vendor_dir.mkdir(parents=True, exist_ok=True)
        (vendor_dir / "ranked_portfolio.json").write_text(json.dumps(rows), encoding="utf-8")
        (vendor_dir / "evidence.jsonl").write_text("", encoding="utf-8")
        reg_path = self._mp_root / "_portfolio" / "master_account_plan_registry.json"
        reg = json.loads(reg_path.read_text())
        if not any(e.get("vendor_slug") == vendor_slug for e in reg["registry"]):
            reg["registry"].append({"vendor_slug": vendor_slug})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")


class TestPriorityAccounts(_IsolatedFixtureMixin):
    def test_resolves_customers_prospects_account_to_real_entity(self):
        self._write_graph(_graph_with(entities=[DEL_TACO]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        graph = ei._read_graph()
        accounts, unresolved = pas._priority_accounts(graph)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["entity_id"], "brand-del-taco")
        self.assertEqual(accounts[0]["aliases"], ["Del Taco Restaurants"])
        self.assertEqual(accounts[0]["evidence_target"], {"kind": "customers_prospects", "slug": "del-taco"})
        self.assertEqual(unresolved, [])

    def test_unresolved_account_reported_not_dropped_or_guessed(self):
        self._write_graph(_graph_with(entities=[]))
        self._add_customers_prospects_account("ghost-brand", "Totally Unknown Brand")
        graph = ei._read_graph()
        accounts, unresolved = pas._priority_accounts(graph)
        self.assertEqual(accounts, [])
        self.assertIn("Totally Unknown Brand", unresolved)

    def test_resolves_map_only_row_via_entity_slug_for_row(self):
        self._write_graph(_graph_with(entities=[BLAZE]))
        self._add_mp_vendor_row("worldpay", [
            {"account_name": "Blaze Pizza", "priority": "P1", "tier": "Tier 1"},
        ])
        graph = ei._read_graph()
        accounts, unresolved = pas._priority_accounts(graph)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["entity_id"], "brand-blaze-pizza")
        self.assertEqual(accounts[0]["evidence_target"]["kind"], "master_account_plan")
        self.assertEqual(accounts[0]["evidence_target"]["vendor_slug"], "worldpay")

    def test_same_entity_from_both_sources_deduplicated_customers_prospects_wins(self):
        self._write_graph(_graph_with(entities=[DEL_TACO]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        self._add_mp_vendor_row("worldpay", [{"account_name": "Del Taco", "priority": "P1"}])
        graph = ei._read_graph()
        accounts, _ = pas._priority_accounts(graph)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["evidence_target"]["kind"], "customers_prospects")

    def test_excludes_own_company_from_customers_prospects_source(self):
        """RB-2026-09-10, confirmed live: customers_prospects_registry.json
        has a real "worldpay" account entry (Todd's own employer, tracked
        there for a different reason) that resolved as a genuine priority
        account and got scanned for trade-press coverage about itself.
        Same exclusion tech_stack_relationship_promotion.py already applies
        via the same core.GP_OWN_TERMS list."""
        self._write_graph(_graph_with(entities=[]))
        self._add_customers_prospects_account("worldpay", "Worldpay")
        graph = ei._read_graph()
        accounts, unresolved = pas._priority_accounts(graph)
        self.assertEqual(accounts, [])
        self.assertEqual(unresolved, [])  # excluded outright, not a resolution failure

    def test_excludes_own_company_from_master_account_plan_source(self):
        self._write_graph(_graph_with(entities=[]))
        self._add_mp_vendor_row("worldpay", [{"account_name": "Genius", "priority": "P1"}])
        graph = ei._read_graph()
        accounts, unresolved = pas._priority_accounts(graph)
        self.assertEqual(accounts, [])
        self.assertEqual(unresolved, [])


class TestFetchPublisherArticles(unittest.TestCase):
    def test_positive_match(self):
        fixture = _rss_bytes([{
            "title": "Del Taco Activates Project Del Sunrise Roadmap With All-New Catering Platform",
            "url": "https://www.restaurantnews.com/del-taco-catering-090226/",
            "pub_date": "Wed, 02 Sep 2026 21:45:21 GMT",
        }])
        fetcher = lambda url, timeout: fixture
        articles = pas._fetch_publisher_articles("Del Taco", fetcher=fetcher)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["url"], "https://www.restaurantnews.com/del-taco-catering-090226/")
        self.assertEqual(articles[0]["pub_date"], "2026-09-02")

    def test_alias_match(self):
        fixture = _rss_bytes([{
            "title": "Del Taco Restaurants Reports Strong Q3 Franchise Growth",
            "url": "https://www.nrn.com/del-taco-restaurants-q3-090826/",
            "pub_date": "Tue, 08 Sep 2026 10:00:00 GMT",
        }])
        fetcher = lambda url, timeout: fixture
        articles = pas._fetch_publisher_articles("Del Taco Restaurants", fetcher=fetcher)
        self.assertEqual(len(articles), 1)

    def test_irrelevant_brand_mention_rejected(self):
        """RB-2026-09-06 class bug (entity_alerts._mentions_entity): a
        headline that superficially cleared Google News's quoted-phrase
        search but never actually names the entity must be rejected."""
        fixture = _rss_bytes([{
            "title": "ABC Stores Hires New CIO to Lead Digital Transformation",
            "url": "https://www.restaurantdive.com/abc-stores-cio-090126/",
            "pub_date": "Tue, 01 Sep 2026 09:00:00 GMT",
        }])
        fetcher = lambda url, timeout: fixture
        articles = pas._fetch_publisher_articles("Del Taco", fetcher=fetcher)
        self.assertEqual(articles, [])

    def test_fetch_failure_returns_none_not_empty_list(self):
        fetcher = lambda url, timeout: None
        result = pas._fetch_publisher_articles("Del Taco", fetcher=fetcher)
        self.assertIsNone(result)


class TestMaterialSignalClass(unittest.TestCase):
    def test_material_headline_classified_when_a_known_vendor_is_named(self):
        self.assertEqual(
            pas._material_signal_class(MATERIAL_HEADLINE, ["Toast"]),
            "vendor_relationship_formed",
        )

    def test_non_material_headline_dropped(self):
        self.assertIsNone(pas._material_signal_class("Del Taco Brings Back Fan-Favorite Fall Menu Item", ["Toast"]))

    def test_vendor_relationship_formed_requires_a_known_vendor_name(self):
        """RB-2026-09-10, confirmed live: generic "rolls out"/"rolling out"
        language matches real menu-item/program launches just as easily as
        real vendor adoption -- "Slim Chickens Rolling Out Bacon Ranch
        Chicken Sandwich" and "Golden Corral rolls out brunch systemwide"
        both classified as vendor_relationship_formed despite naming no
        vendor at all. Requiring a real, known vendor name in the headline
        closes this without touching the shared ecosystem_brief.py
        taxonomy other pipelines rely on unchanged."""
        self.assertIsNone(
            pas._material_signal_class("Slim Chickens Rolling Out Bacon Ranch Chicken Sandwich", ["Toast", "Oracle"])
        )
        self.assertIsNone(
            pas._material_signal_class("Golden Corral rolls out brunch systemwide", ["Toast", "Oracle"])
        )

    def test_vendor_relationship_formed_passes_with_no_known_vendors_configured(self):
        """An empty known_vendor_names list (e.g. a graph with zero vendor
        entities) must not silently pass everything through -- absence of
        vendor data should behave the same as absence of a matching
        vendor name."""
        self.assertIsNone(pas._material_signal_class(MATERIAL_HEADLINE, []))


class TestScanEndToEnd(_IsolatedFixtureMixin):
    def _fetcher_for(self, by_query_substring: dict[str, bytes]):
        def fetcher(url, timeout):
            for needle, body in by_query_substring.items():
                if needle in url:
                    return body
            return _rss_bytes([])
        return fetcher

    def test_scan_queues_a_material_positive_match(self):
        self._write_graph(_graph_with(entities=[DEL_TACO, TOAST]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        fixture = _rss_bytes([{
            "title": MATERIAL_HEADLINE,
            "url": "https://www.restaurantnews.com/del-taco-payments-090226/",
            "pub_date": "Wed, 02 Sep 2026 21:45:21 GMT",
        }])
        result = pas.scan(fetcher=lambda url, timeout: fixture)
        self.assertEqual(result["material_queued"], 1)
        self.assertEqual(result["auto_applied"], 1)
        self.assertEqual(result["fetch_failures"], [])
        candidates = self._all_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["entity_id"], "brand-del-taco")
        self.assertEqual(candidates[0]["pub_date"], "2026-09-02")
        self.assertEqual(candidates[0]["status"], "confirmed")
        self.assertEqual(candidates[0]["confirmed_by"], "system:priority_account_publisher_scan")

    def test_scan_drops_non_material_hit(self):
        self._write_graph(_graph_with(entities=[DEL_TACO, TOAST]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        fixture = _rss_bytes([{
            "title": "Del Taco Brings Back Fan-Favorite Fall Menu Item",
            "url": "https://www.restaurantnews.com/del-taco-fall-menu-090226/",
            "pub_date": "Wed, 02 Sep 2026 21:45:21 GMT",
        }])
        result = pas.scan(fetcher=lambda url, timeout: fixture)
        self.assertEqual(result["material_queued"], 0)
        self.assertEqual(pas.pending_candidates(), [])

    def test_scan_skips_url_already_a_known_graph_source(self):
        """The real RB-DEFECT-069 case: the Del Taco article was already
        captured as a graph source via a different path (manual research)
        before this scanner ever ran -- must not re-propose it."""
        self._write_graph(_graph_with(entities=[DEL_TACO, TOAST], sources=[{
            "id": "src-del-taco-catering", "url": "https://www.restaurantnews.com/del-taco-catering-090226/",
            "source_type": "credible_trade_reporting", "title": "x", "published_at": "2026-09-02",
        }]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        fixture = _rss_bytes([{
            "title": MATERIAL_HEADLINE,
            "url": "https://www.restaurantnews.com/del-taco-catering-090226/",
            "pub_date": "Wed, 02 Sep 2026 21:45:21 GMT",
        }])
        result = pas.scan(fetcher=lambda url, timeout: fixture)
        self.assertEqual(result["material_queued"], 0)
        self.assertEqual(result["duplicates_skipped"], 1)

    def test_scan_skips_url_already_in_account_evidence(self):
        self._write_graph(_graph_with(entities=[DEL_TACO, TOAST]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        evidence_path = self._cpc_root / "accounts" / "del-taco" / "evidence.jsonl"
        evidence_path.write_text(json.dumps({
            "evidence_id": "ev-del-taco-0001", "durable_source_id": "https://www.restaurantnews.com/del-taco-catering-090226/",
        }) + "\n", encoding="utf-8")
        fixture = _rss_bytes([{
            "title": MATERIAL_HEADLINE,
            "url": "https://www.restaurantnews.com/del-taco-catering-090226/",
            "pub_date": "Wed, 02 Sep 2026 21:45:21 GMT",
        }])
        result = pas.scan(fetcher=lambda url, timeout: fixture)
        self.assertEqual(result["material_queued"], 0)

    def test_repeated_run_does_not_duplicate_the_same_candidate(self):
        self._write_graph(_graph_with(entities=[DEL_TACO, TOAST]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        fixture = _rss_bytes([{
            "title": MATERIAL_HEADLINE,
            "url": "https://www.restaurantnews.com/del-taco-payments-090226/",
            "pub_date": "Wed, 02 Sep 2026 21:45:21 GMT",
        }])
        pas.scan(fetcher=lambda url, timeout: fixture)
        result2 = pas.scan(fetcher=lambda url, timeout: fixture)
        self.assertEqual(result2["material_queued"], 0)
        self.assertEqual(len(self._all_candidates()), 1)

    def test_publisher_fetch_failure_reported_not_raised(self):
        self._write_graph(_graph_with(entities=[DEL_TACO, TOAST]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        result = pas.scan(fetcher=lambda url, timeout: None)
        self.assertIn("Del Taco", result["fetch_failures"])
        self.assertEqual(result["material_queued"], 0)

    def test_dry_run_does_not_persist_candidates(self):
        self._write_graph(_graph_with(entities=[DEL_TACO, TOAST]))
        self._add_customers_prospects_account("del-taco", "Del Taco")
        fixture = _rss_bytes([{
            "title": MATERIAL_HEADLINE,
            "url": "https://www.restaurantnews.com/del-taco-payments-090226/",
            "pub_date": "Wed, 02 Sep 2026 21:45:21 GMT",
        }])
        pas.scan(dry_run=True, fetcher=lambda url, timeout: fixture)
        self.assertFalse(pas.STORE_PATH.exists())


class TestRecordProposal(_IsolatedFixtureMixin):
    def _seed_pending(self, evidence_target):
        pas.STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        store = {"candidates": {"cand-1": {
            "candidate_id": "cand-1", "status": "proposed_pending_confirmation",
            "entity_id": "brand-del-taco", "account_name": "Del Taco",
            "evidence_target": evidence_target, "signal_class": "vendor_relationship_formed",
            "title": MATERIAL_HEADLINE,
            "url": "https://www.restaurantnews.com/del-taco-payments-090226/",
            "pub_date": "2026-09-02", "source_name": "Industry Publisher Search",
            "detected_at": "2026-09-10T00:00:00Z", "resolved_at": None,
        }}}
        pas._save_store(store)

    def test_confirm_appends_reference_evidence_with_real_pub_date(self):
        self._add_customers_prospects_account("del-taco", "Del Taco")
        self._seed_pending({"kind": "customers_prospects", "slug": "del-taco"})
        result = pas.record_proposal("cand-1", confirmed=True)
        self.assertTrue(result["confirmed"])
        evidence = cpc.load_jsonl(self._cpc_root / "accounts" / "del-taco" / "evidence.jsonl")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["event_date"], "2026-09-02")
        self.assertEqual(evidence[0]["durable_source_id"], "https://www.restaurantnews.com/del-taco-payments-090226/")
        self.assertEqual(evidence[0]["source_type"], "priority_account_publisher_match")
        self.assertEqual(evidence[0]["extracted_claims"], [])

    def test_confirm_writes_master_account_plan_evidence_when_no_customers_prospects_account(self):
        self._add_mp_vendor_row("worldpay", [{"account_name": "Del Taco"}])
        self._seed_pending({"kind": "master_account_plan", "vendor_slug": "worldpay", "account_name": "Del Taco"})
        result = pas.record_proposal("cand-1", confirmed=True)
        self.assertTrue(result["confirmed"])
        evidence = mp_common.load_jsonl(self._mp_root / "vendors" / "worldpay" / "evidence.jsonl")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["event_at"], "2026-09-02")

    def test_reject_marks_rejected_without_writing_evidence(self):
        self._add_customers_prospects_account("del-taco", "Del Taco")
        self._seed_pending({"kind": "customers_prospects", "slug": "del-taco"})
        result = pas.record_proposal("cand-1", confirmed=False)
        self.assertTrue(result["rejected"])
        evidence = cpc.load_jsonl(self._cpc_root / "accounts" / "del-taco" / "evidence.jsonl")
        self.assertEqual(evidence, [])

    def test_cannot_resolve_twice(self):
        self._add_customers_prospects_account("del-taco", "Del Taco")
        self._seed_pending({"kind": "customers_prospects", "slug": "del-taco"})
        pas.record_proposal("cand-1", confirmed=True)
        result = pas.record_proposal("cand-1", confirmed=True)
        self.assertIn("error", result)

    def test_unknown_candidate_id_errors(self):
        result = pas.record_proposal("not-a-real-id", confirmed=True)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
