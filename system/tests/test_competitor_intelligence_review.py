"""
test_competitor_intelligence_review.py — RB-2026-09-01.

Coverage for competitor_intelligence_review.py, the safe-apply vs.
flag-for-review scan for Competitor Intelligence, mirroring
master_account_plans/_engine/mp_impact_review.py's discipline. Isolated
from real production data throughout -- own tmp graph, own tmp
competitor_intelligence root, combining test_competitor_intelligence.py's
tmp registry-root pattern with test_competitive_landscape.py's tmp graph
pattern.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ei = _load("ecosystem_intelligence", "ecosystem_intelligence.py")
sys.modules["ecosystem_intelligence"] = ei
sys.path.insert(0, str(SCRIPTS_DIR))

import competitor_intelligence_common as cic  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_review as cir  # noqa: E402
import intelligence_db as idb  # noqa: E402


def _graph(signals=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-01",
        "entities": [], "relationships": [], "signals": signals or [],
        "sources": [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _material_signal(sig_id="sig-1", vendor_entity_id="vendor-qu", confidence="high", sig_class="leadership_change"):
    return {
        "id": sig_id, "entities": [vendor_entity_id], "signal_type": sig_class,
        "confidence": {"level": confidence}, "summary": "Real signal summary.",
    }


class _IsolatedMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path

        self._orig_root = cic.ROOT
        cic.ROOT = tmp / "competitor_intelligence"

        # Isolate the capture-signal pass from the real production
        # intelligence.db -- without this, run_review_scan's new
        # _capture_signals_for_vendor pass would query real captured
        # intelligence and could queue unexpected capture_signal_review
        # items (or match real "Qu"/"PAR" mentions), breaking every
        # reviews_queued count assertion below non-deterministically.
        self._idb_path = tmp / "intelligence.db"
        self._orig_idb_path = cir.idb.DEFAULT_DB_PATH
        cir.idb.DEFAULT_DB_PATH = self._idb_path

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        cic.ROOT = self._orig_root
        cir.idb.DEFAULT_DB_PATH = self._orig_idb_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.parent.mkdir(parents=True, exist_ok=True)
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _register(self, slug: str, vendor_entity_id: str) -> None:
        cic.create_competitor_shell(slug, slug, vendor_entity_id)
        cic.register_competitor(slug, slug)


class TestMaterialSignalPass(_IsolatedMixin):
    def test_material_signal_queues_one_review(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph([_material_signal()]))
        result = cir.run_review_scan()
        self.assertEqual(result["reviews_queued"], 1)
        queue = cic.load_review_queue()
        self.assertEqual(len(queue["pending_reviews"]), 1)
        item = queue["pending_reviews"][0]
        self.assertEqual(item["competitor_slug"], "qu")
        self.assertEqual(item["kind"], "material_signal_review")
        self.assertEqual(item["evidence_id"], "eco-sig-1")
        self.assertEqual(item["status"], "pending")
        self.assertIsNone(item["category"])

    def test_rerun_never_double_queues_same_signal(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph([_material_signal()]))
        cir.run_review_scan()
        result2 = cir.run_review_scan()
        self.assertEqual(result2["reviews_queued"], 0)
        queue = cic.load_review_queue()
        self.assertEqual(len(queue["pending_reviews"]), 1)

    def test_non_material_signal_never_queued(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph([_material_signal(confidence="low", sig_class="leadership_change")]))
        result = cir.run_review_scan()
        self.assertEqual(result["reviews_queued"], 0)

    def test_wrong_signal_class_never_queued(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph([_material_signal(sig_class="not_a_material_class")]))
        result = cir.run_review_scan()
        self.assertEqual(result["reviews_queued"], 0)

    def test_signal_for_untracked_vendor_never_queued(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph([_material_signal(vendor_entity_id="vendor-someone-else")]))
        result = cir.run_review_scan()
        self.assertEqual(result["reviews_queued"], 0)


class TestStalenessPass(_IsolatedMixin):
    def test_stale_card_queues_once(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph())
        compintel.upsert_category_battle_card("qu", "pos", status="draft")
        comp_path = cic.competitor_dir("qu") / "competitor.json"
        comp = cic.load_json(comp_path)
        comp["category_battle_cards"]["pos"]["last_validated"] = (date.today() - timedelta(days=200)).isoformat()
        cic.save_json(comp_path, comp)

        result = cir.run_review_scan(stale_after_days=90)
        self.assertEqual(result["reviews_queued"], 1)
        queue = cic.load_review_queue()
        item = queue["pending_reviews"][0]
        self.assertEqual(item["kind"], "staleness_review")
        self.assertEqual(item["category"], "pos")

    def test_not_yet_stale_not_queued(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph())
        compintel.upsert_category_battle_card("qu", "pos", status="draft")  # last_validated = today
        result = cir.run_review_scan(stale_after_days=90)
        self.assertEqual(result["reviews_queued"], 0)

    def test_rerun_never_double_queues_same_staleness(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph())
        compintel.upsert_category_battle_card("qu", "pos", status="draft")
        comp_path = cic.competitor_dir("qu") / "competitor.json"
        comp = cic.load_json(comp_path)
        comp["category_battle_cards"]["pos"]["last_validated"] = (date.today() - timedelta(days=200)).isoformat()
        cic.save_json(comp_path, comp)

        cir.run_review_scan(stale_after_days=90)
        result2 = cir.run_review_scan(stale_after_days=90)
        self.assertEqual(result2["reviews_queued"], 0)

    def test_already_stale_status_not_requeued(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph())
        compintel.upsert_category_battle_card("qu", "pos", status="stale")
        comp_path = cic.competitor_dir("qu") / "competitor.json"
        comp = cic.load_json(comp_path)
        comp["category_battle_cards"]["pos"]["last_validated"] = (date.today() - timedelta(days=200)).isoformat()
        cic.save_json(comp_path, comp)

        result = cir.run_review_scan(stale_after_days=90)
        self.assertEqual(result["reviews_queued"], 0)

    def test_do_not_use_status_not_requeued(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph())
        compintel.upsert_category_battle_card("qu", "pos", status="do_not_use")
        comp_path = cic.competitor_dir("qu") / "competitor.json"
        comp = cic.load_json(comp_path)
        comp["category_battle_cards"]["pos"]["last_validated"] = (date.today() - timedelta(days=200)).isoformat()
        cic.save_json(comp_path, comp)

        result = cir.run_review_scan(stale_after_days=90)
        self.assertEqual(result["reviews_queued"], 0)


def _seed_capture_item(db_path: Path, *, entity: str, intelligence_type: str,
                        content: str = "Captured note.") -> str:
    db = idb.open_db(db_path)
    try:
        return db.add_item(
            title=f"{intelligence_type} [{entity}]",
            content=content,
            source_name="capture",
            source_type="unknown",
            gathered_date="2026-09-13",
            confidence="medium",
            lifecycle_state="new",
            tags=[{"type": "entity", "value": entity}, {"type": "keyword", "value": intelligence_type}],
            raw_json={"triage_stream": {"intelligence_type": intelligence_type}, "capture_id": "cap-test"},
        )
    finally:
        db.close()


class TestCaptureSignalPass(_IsolatedMixin):
    """RB-DEFECT (2026-09-14): closes the gap where a captured/pasted
    competitive note about a tracked competitor never reached the review
    queue -- see competitor_intelligence_review.py's module docstring for
    the live Toast incident this fixes."""

    def test_micro_graph_enrichment_capture_queues_one_review(self):
        self._register("toast", "vendor-toast")
        self._write_graph(_graph())
        item_id = _seed_capture_item(self._idb_path, entity="Toast", intelligence_type="micro_graph_enrichment",
                                      content="Toast enterprise ARR expected to double to $200M.")
        result = cir.run_review_scan()
        self.assertEqual(result["reviews_queued"], 1)
        queue = cic.load_review_queue()
        item = queue["pending_reviews"][0]
        self.assertEqual(item["competitor_slug"], "toast")
        self.assertEqual(item["kind"], "capture_signal_review")
        self.assertEqual(item["evidence_id"], f"capture-{item_id}")
        self.assertIn("Toast enterprise ARR", item["reason"])

    def test_micro_graph_build_capture_also_queued(self):
        self._register("toast", "vendor-toast")
        self._write_graph(_graph())
        _seed_capture_item(self._idb_path, entity="Toast", intelligence_type="micro_graph_build")
        result = cir.run_review_scan()
        self.assertEqual(result["reviews_queued"], 1)

    def test_macro_signal_capture_never_queued(self):
        """macro_signal items can name a dozen unrelated companies in one
        blob (entity_scoped only when exactly one watchlist entity matched)
        -- deliberately excluded, see module docstring."""
        self._register("toast", "vendor-toast")
        self._write_graph(_graph())
        _seed_capture_item(self._idb_path, entity="Toast", intelligence_type="macro_signal")
        result = cir.run_review_scan()
        self.assertEqual(result["reviews_queued"], 0)

    def test_capture_for_untracked_competitor_never_queued(self):
        self._register("toast", "vendor-toast")
        self._write_graph(_graph())
        _seed_capture_item(self._idb_path, entity="Some Unrelated Company",
                            intelligence_type="micro_graph_enrichment")
        result = cir.run_review_scan()
        self.assertEqual(result["reviews_queued"], 0)

    def test_rerun_never_double_queues_same_capture(self):
        self._register("toast", "vendor-toast")
        self._write_graph(_graph())
        _seed_capture_item(self._idb_path, entity="Toast", intelligence_type="micro_graph_enrichment")
        cir.run_review_scan()
        result2 = cir.run_review_scan()
        self.assertEqual(result2["reviews_queued"], 0)
        queue = cic.load_review_queue()
        self.assertEqual(len(queue["pending_reviews"]), 1)


class TestReviewNeverMutatesBattleCardContent(_IsolatedMixin):
    def test_scan_never_writes_to_category_battle_cards(self):
        self._register("qu", "vendor-qu")
        self._write_graph(_graph([_material_signal()]))
        compintel.upsert_category_battle_card("qu", "pos", status="draft", rm_plain_english_posture="original")
        cir.run_review_scan()
        data = cic.load_competitor("qu")
        self.assertEqual(data["competitor"]["category_battle_cards"]["pos"]["status"], "draft")
        self.assertEqual(data["competitor"]["category_battle_cards"]["pos"]["rm_plain_english_posture"], "original")


if __name__ == "__main__":
    unittest.main()
