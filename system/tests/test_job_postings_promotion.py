"""
test_job_postings_promotion.py — RB-2026-09-18, next-sprint Workstream 3.

Coverage for job_postings_promotion.py, the review-first scanner built to
close a real gap: vulnerability_taxonomy.py's tech_hiring category already
classifies "Director of Restaurant Technology"/"POS Program Manager"-shaped
role titles and is already wired into entity_alerts.py/
competitive_vulnerability.py's scoring, but nothing ever acquired a real job
posting to classify -- that pipeline only ever sees Google News RSS results,
which never surface a job-board listing. See the module's own docstring for
the acquisition method (DuckDuckGo HTML search, no paid API) and the
deliberate v1 scope (customers_prospects accounts only).

Isolated throughout (own tmp graph, own tmp customers_prospects/ tree, own
tmp candidate store, injected fetcher -- never a real network call). Same
isolation discipline as test_priority_account_publisher_scan.py.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ecosystem_intelligence as ei  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import job_postings_promotion as jp  # noqa: E402


def _graph_with(entities=None, sources=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-18",
        "entities": entities or [], "relationships": [], "signals": [],
        "sources": sources or [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _entity(entity_id, name, entity_type="brand", aliases=None):
    return {"id": entity_id, "name": name, "entity_type": entity_type, "aliases": aliases or [],
            "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}


WENDYS = _entity("brand-wendys", "Wendy's")
GENIUS = _entity("vendor-genius", "Genius", entity_type="vendor")


def _ddg_html(results: list[tuple[str, str]]) -> str:
    """results: list of (title, real_url) -- wraps each in DuckDuckGo's real
    result__a anchor + uddg= redirect shape that _RESULT_RE/uddg parsing expect."""
    import urllib.parse
    parts = ["<html><body>"]
    for title, url in results:
        encoded = urllib.parse.quote(url, safe="")
        parts.append(
            f'<a class="result__a" href="//duckduckgo.com/l/?uddg={encoded}&amp;rut=x">{title}</a>'
        )
    parts.append("</body></html>")
    return "".join(parts)


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)

        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._store_path = tmp / "job_postings_candidates.json"

        cpc_root = tmp / "customers_prospects"
        (cpc_root / "_portfolio").mkdir(parents=True)

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_store_path = jp.STORE_PATH
        self._orig_cpc_root = cpc.ROOT

        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        jp.STORE_PATH = self._store_path
        cpc.ROOT = cpc_root
        self._cpc_root = cpc_root

        (cpc_root / "_portfolio" / "customers_prospects_registry.json").write_text(
            json.dumps({"registry": []}), encoding="utf-8",
        )

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        jp.STORE_PATH = self._orig_store_path
        cpc.ROOT = self._orig_cpc_root
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _add_account(self, slug: str, display_name: str) -> None:
        acct_dir = self._cpc_root / "accounts" / slug
        acct_dir.mkdir(parents=True)
        (acct_dir / "account.json").write_text(json.dumps({"display_name": display_name}), encoding="utf-8")
        (acct_dir / "evidence.jsonl").write_text("", encoding="utf-8")
        reg_path = self._cpc_root / "_portfolio" / "customers_prospects_registry.json"
        reg = json.loads(reg_path.read_text())
        reg["registry"].append({"account_id": f"acct-{slug}", "status": "active", "aliases": []})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")

    def _all_candidates(self) -> list[dict]:
        """Confidence-Based Auto-Recording Phase 5/7 (2026-09-25): scan()
        now auto-confirms a qualifying candidate immediately, so pending_
        candidates() is empty right after a scan -- tests inspecting what
        a scan just produced read the full candidate store instead."""
        return list(jp._load_store()["candidates"].values())


class TestTargetAccounts(_IsolatedFixtureMixin):
    def test_resolves_customers_prospects_account_to_real_entity(self):
        self._write_graph(_graph_with(entities=[WENDYS]))
        self._add_account("wendys", "Wendy's")
        graph = ei._read_graph()
        accounts, unresolved = jp._target_accounts(graph)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["entity_id"], "brand-wendys")
        self.assertEqual(accounts[0]["slug"], "wendys")
        self.assertEqual(unresolved, [])

    def test_unresolved_account_reported_not_dropped(self):
        self._write_graph(_graph_with(entities=[]))
        self._add_account("ghost-brand", "Totally Unknown Brand")
        graph = ei._read_graph()
        accounts, unresolved = jp._target_accounts(graph)
        self.assertEqual(accounts, [])
        self.assertIn("Totally Unknown Brand", unresolved)

    def test_excludes_own_company(self):
        self._write_graph(_graph_with(entities=[]))
        self._add_account("worldpay", "Worldpay")
        graph = ei._read_graph()
        accounts, unresolved = jp._target_accounts(graph)
        self.assertEqual(accounts, [])
        self.assertEqual(unresolved, [])  # excluded outright, not a resolution failure


class TestTechHiringSignal(unittest.TestCase):
    def test_real_tech_hiring_title_is_material(self):
        self.assertTrue(jp._tech_hiring_signal("Director of Restaurant Technology"))
        self.assertTrue(jp._tech_hiring_signal("POS Program Manager"))

    def test_generic_role_is_not_material(self):
        self.assertFalse(jp._tech_hiring_signal("Shift Manager"))
        self.assertFalse(jp._tech_hiring_signal("Crew Member"))


class TestSearchJobPostings(unittest.TestCase):
    def test_positive_match_parsed_and_relevance_checked(self):
        fixture = _ddg_html([
            ("Director of Restaurant Technology - Wendy's", "https://boards.greenhouse.io/wendys/jobs/12345"),
        ])
        fetcher = lambda url: fixture
        postings = jp._search_job_postings("Wendy's", fetcher=fetcher)
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0]["url"], "https://boards.greenhouse.io/wendys/jobs/12345")
        self.assertEqual(postings[0]["title"], "Director of Restaurant Technology - Wendy's")

    def test_irrelevant_result_dropped(self):
        """DDG's site: search restricts domain but not text relevance --
        same _mentions_entity relevance gate entity_alerts.py already needed
        for Google News RSS (RB-2026-09-06) applies here too."""
        fixture = _ddg_html([
            ("Director of Restaurant Technology - McDonald's", "https://boards.greenhouse.io/mcdonalds/jobs/1"),
        ])
        fetcher = lambda url: fixture
        postings = jp._search_job_postings("Wendy's", fetcher=fetcher)
        self.assertEqual(postings, [])

    def test_fetch_failure_returns_none_not_empty_list(self):
        fetcher = lambda url: None
        self.assertIsNone(jp._search_job_postings("Wendy's", fetcher=fetcher))


class TestScan(_IsolatedFixtureMixin):
    def test_material_posting_becomes_a_candidate(self):
        self._write_graph(_graph_with(entities=[WENDYS]))
        self._add_account("wendys", "Wendy's")
        fixture = _ddg_html([
            ("Director of Restaurant Technology - Wendy's", "https://boards.greenhouse.io/wendys/jobs/12345"),
        ])
        result = jp.scan(fetcher=lambda url: fixture)
        self.assertEqual(result["material_queued"], 1)
        self.assertEqual(result["auto_applied"], 1)
        candidates = self._all_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["entity_id"], "brand-wendys")
        self.assertEqual(candidates[0]["vulnerability_category"], "tech_hiring")
        self.assertEqual(candidates[0]["status"], "confirmed")
        self.assertEqual(candidates[0]["confirmed_by"], "system:job_postings_promotion")
        evidence_path = self._cpc_root / "accounts" / "wendys" / "evidence.jsonl"
        records = cpc.load_jsonl(evidence_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_type"], "job_postings_promotion_match")

    def test_generic_role_produces_no_candidate(self):
        self._write_graph(_graph_with(entities=[WENDYS]))
        self._add_account("wendys", "Wendy's")
        fixture = _ddg_html([("Shift Manager - Wendy's", "https://boards.greenhouse.io/wendys/jobs/999")])
        result = jp.scan(fetcher=lambda url: fixture)
        self.assertEqual(result["material_queued"], 0)
        self.assertEqual(jp.pending_candidates(), [])

    def test_unresolved_brand_produces_no_candidate(self):
        """No entity match -- must never guess or mutate the wrong entity
        (Todd's canonical mutation policy: identity ambiguity routes to
        review, never a silent mutation; here, never even a candidate)."""
        self._write_graph(_graph_with(entities=[]))
        self._add_account("ghost-brand", "Totally Unknown Brand")
        result = jp.scan(fetcher=lambda url: _ddg_html([
            ("Director of Restaurant Technology - Totally Unknown Brand",
             "https://boards.greenhouse.io/ghost/jobs/1"),
        ]))
        self.assertEqual(result["material_queued"], 0)
        self.assertIn("Totally Unknown Brand", result["unresolved_accounts"])

    def test_rescan_does_not_duplicate_candidate(self):
        self._write_graph(_graph_with(entities=[WENDYS]))
        self._add_account("wendys", "Wendy's")
        fixture = _ddg_html([
            ("Director of Restaurant Technology - Wendy's", "https://boards.greenhouse.io/wendys/jobs/12345"),
        ])
        jp.scan(fetcher=lambda url: fixture)
        result2 = jp.scan(fetcher=lambda url: fixture)
        self.assertEqual(result2["material_queued"], 0)
        self.assertEqual(len(self._all_candidates()), 1)

    def test_dry_run_does_not_persist_candidate(self):
        self._write_graph(_graph_with(entities=[WENDYS]))
        self._add_account("wendys", "Wendy's")
        fixture = _ddg_html([
            ("Director of Restaurant Technology - Wendy's", "https://boards.greenhouse.io/wendys/jobs/12345"),
        ])
        jp.scan(dry_run=True, fetcher=lambda url: fixture)
        self.assertEqual(jp.pending_candidates(), [])

    def test_fetch_failure_recorded_not_silently_swallowed(self):
        self._write_graph(_graph_with(entities=[WENDYS]))
        self._add_account("wendys", "Wendy's")
        result = jp.scan(fetcher=lambda url: None)
        self.assertIn("Wendy's", result["fetch_failures"])


class TestRecordProposal(_IsolatedFixtureMixin):
    """record_proposal() itself, exercised directly against a manually-
    seeded pending candidate -- independent of scan() now auto-confirming
    (Phase 5/7, 2026-09-25), since record_proposal() must keep working
    correctly on its own (e.g. a candidate resolved via a still-open
    review surface, or seeded some other way)."""

    def _seed_pending(self) -> str:
        self._write_graph(_graph_with(entities=[WENDYS]))
        self._add_account("wendys", "Wendy's")
        store = jp._load_store()
        jp._add_candidate(store, account={
            "entity_id": "brand-wendys", "name": "Wendy's", "slug": "wendys", "aliases": [],
        }, posting={
            "title": "Director of Restaurant Technology - Wendy's",
            "url": "https://boards.greenhouse.io/wendys/jobs/12345",
            "source_name": "DuckDuckGo Job Board Search",
        })
        jp._save_store(store)
        return jp._candidate_id(
            "brand-wendys", "https://boards.greenhouse.io/wendys/jobs/12345",
            "Director of Restaurant Technology - Wendy's",
        )

    def test_confirm_appends_fact_free_evidence(self):
        cid = self._seed_pending()
        result = jp.record_proposal(cid, confirmed=True)
        self.assertTrue(result["confirmed"])
        evidence_path = self._cpc_root / "accounts" / "wendys" / "evidence.jsonl"
        records = cpc.load_jsonl(evidence_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_type"], "job_postings_promotion_match")
        self.assertEqual(records[0]["durable_source_id"], "https://boards.greenhouse.io/wendys/jobs/12345")
        self.assertEqual(records[0]["extracted_claims"], [])  # fact-free, never a structured claim

    def test_reject_writes_no_evidence(self):
        cid = self._seed_pending()
        result = jp.record_proposal(cid, confirmed=False)
        self.assertTrue(result["rejected"])
        evidence_path = self._cpc_root / "accounts" / "wendys" / "evidence.jsonl"
        self.assertEqual(cpc.load_jsonl(evidence_path), [])

    def test_cannot_resolve_twice(self):
        cid = self._seed_pending()
        jp.record_proposal(cid, confirmed=True)
        result = jp.record_proposal(cid, confirmed=True)
        self.assertIn("error", result)

    def test_unknown_candidate_id_errors(self):
        result = jp.record_proposal("not-a-real-id", confirmed=True)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
