"""
test_capture_entity_tag_scoping.py

Regression coverage: three capture-ingestion write paths in server.py
(POST /captures/{id}/submit, POST /captures/process_all, POST /ingest)
tagged EVERY triage stream's extracted_entities as tag_type="entity" in
IntelligenceDB, regardless of the stream's own entity_scoped flag.

extracted_entities holds real named entities only for _classify_micro_graph
streams (entity_scoped=True) -- for every other classifier (macro, ri,
strategic, career, etc., all entity_scoped=False) it holds category/type
labels instead (see intelligence_triage._classify_macro:
"extracted_entities": list(sorted(categories_hit))). Tagging those as
tag_type="entity" wrote literal category names like "industry_trend" and
"vendor_tech" into the DB as if they were named companies. Confirmed live
in intelligence.db: an item titled "macro_signal [competitive,
consumer_behavior, industry_trend]" sourced from a "Just Press Record"
capture, tagged entity=industry_trend / entity=vendor_tech / etc. --
which then surfaced as a nonsense "[ENTITY PAIR] industry_trend +
vendor_tech — co-appear in 13 intelligence items" convergence claim in
the Daily Brief's Connect the Dots section (intelligence_db.
cross_entity_convergence() queries tag_type='entity' verbatim, with no
way to know the tag was ever bogus).

Fixed: all three sites now only tag extracted_entities as tag_type="entity"
when stream.get("entity_scoped") is truthy.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

import capture_ingest  # noqa: E402
from intelligence_db import IntelligenceDB  # noqa: E402


def _tmp_db_path() -> Path:
    return Path(tempfile.mktemp(suffix=".db"))


def _write_pending(pending_dir: Path, file_id: str, transcript: str) -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / f"{file_id}.json").write_text(json.dumps({
        "file_id": file_id,
        "queued_at": "2026-07-13T09:04:51+00:00",
        "source_id": "just_press_record",
        "source_label": "Just Press Record",
        "capture_type": "meeting",
        "title_hint": "macro signal capture",
        "transcript": transcript,
        "word_count": len(transcript.split()),
        "transcript_available": True,
        "status": "pending",
    }), encoding="utf-8")


_MACRO_STREAM_TRIAGE_RESULT = {
    "type_count": 1,
    "noise_only": False,
    "identified_types": [{
        "intelligence_type": "macro_signal",
        "extracted_summary": "Macro/industry signal detected.",
        "extracted_entities": ["industry_trend", "vendor_tech"],
        "entity_scoped": False,
        "confidence": "medium",
    }],
}

_MICRO_GRAPH_STREAM_TRIAGE_RESULT = {
    "type_count": 1,
    "noise_only": False,
    "identified_types": [{
        "intelligence_type": "micro_graph_data",
        "extracted_summary": "McDonald's field office data detected.",
        "extracted_entities": ["McDonald's US Ops"],
        "entity_scoped": True,
        "confidence": "high",
    }],
}


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestCaptureSubmitEntityTagScoping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server  # noqa: PLC0415
        cls.server = server
        cls.client = TestClient(server.app, headers={"x-api-key": "test-key"})
        cls._orig_open_idb = server._open_idb
        cls._db_path = _tmp_db_path()

        def _patched_open_idb():
            db = IntelligenceDB(cls._db_path)
            db.open()
            return db

        server._open_idb = _patched_open_idb

    @classmethod
    def tearDownClass(cls):
        cls.server._open_idb = cls._orig_open_idb

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="rb_capture_entity_scope_"))
        self._orig_pending = capture_ingest.PENDING_DIR
        self._orig_processed = capture_ingest.PROCESSED_DIR
        self._orig_registry = capture_ingest.REGISTRY_PATH
        capture_ingest.PENDING_DIR = self._tmp / "pending"
        capture_ingest.PROCESSED_DIR = self._tmp / "processed"
        capture_ingest.REGISTRY_PATH = self._tmp / ".registry.json"

    def tearDown(self):
        capture_ingest.PENDING_DIR = self._orig_pending
        capture_ingest.PROCESSED_DIR = self._orig_processed
        capture_ingest.REGISTRY_PATH = self._orig_registry

    def _open_test_db(self) -> IntelligenceDB:
        db = IntelligenceDB(self._db_path)
        db.open()
        return db

    def test_macro_signal_categories_not_tagged_as_entity(self):
        _write_pending(capture_ingest.PENDING_DIR, "cap-macro-signal",
                        "Industry-wide unit growth momentum and vendor tech shift discussed.")

        with patch.object(self.server.intelligence_triage, "triage_input",
                           return_value=dict(_MACRO_STREAM_TRIAGE_RESULT)):
            resp = self.client.post("/captures/cap-macro-signal/submit")
        self.assertEqual(resp.status_code, 200)

        db = self._open_test_db()
        try:
            bogus_entity_hits = db.search(tag_type="entity", tag_value="industry_trend")
            self.assertEqual(bogus_entity_hits, [],
                              "macro-signal category label was tagged as tag_type=entity")
            bogus_entity_hits2 = db.search(tag_type="entity", tag_value="vendor_tech")
            self.assertEqual(bogus_entity_hits2, [])
            # The item itself must still be persisted -- only the entity tag is
            # skipped. "intelligence_type" isn't a recognized TAG_TYPES value, so
            # tag_item_bulk's normalizer files it under "keyword" instead.
            macro_items = db.search(tag_type="keyword", tag_value="macro_signal")
            self.assertTrue(macro_items, "macro_signal item was not persisted at all")
        finally:
            db.close()

    def test_entity_scoped_stream_still_tagged_as_entity(self):
        _write_pending(capture_ingest.PENDING_DIR, "cap-micro-graph",
                        "McDonald's field office org chart data discussed.")

        with patch.object(self.server.intelligence_triage, "triage_input",
                           return_value=dict(_MICRO_GRAPH_STREAM_TRIAGE_RESULT)):
            resp = self.client.post("/captures/cap-micro-graph/submit")
        self.assertEqual(resp.status_code, 200)

        db = self._open_test_db()
        try:
            hits = db.search(tag_type="entity", tag_value="McDonald's US Ops")
            self.assertTrue(hits, "entity_scoped stream's real entity was not tagged")
        finally:
            db.close()


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestIngestContentEntityTagScoping(unittest.TestCase):
    """Same fix, third call site: POST /ingest (ingestContent)."""

    @classmethod
    def setUpClass(cls):
        import server  # noqa: PLC0415
        cls.server = server
        cls.client = TestClient(server.app, headers={"x-api-key": "test-key"})
        cls._orig_open_idb = server._open_idb
        cls._db_path = _tmp_db_path()

        def _patched_open_idb():
            db = IntelligenceDB(cls._db_path)
            db.open()
            return db

        server._open_idb = _patched_open_idb

    @classmethod
    def tearDownClass(cls):
        cls.server._open_idb = cls._orig_open_idb

    def _open_test_db(self) -> IntelligenceDB:
        db = IntelligenceDB(self._db_path)
        db.open()
        return db

    def test_macro_signal_categories_not_tagged_as_entity_via_ingest(self):
        with patch.object(self.server.intelligence_triage, "triage_input",
                           return_value=dict(_MACRO_STREAM_TRIAGE_RESULT)):
            resp = self.client.post(
                "/ingest",
                json={"text": "Industry-wide unit growth momentum and vendor tech shift discussed."},
            )
        self.assertEqual(resp.status_code, 200)

        db = self._open_test_db()
        try:
            bogus_entity_hits = db.search(tag_type="entity", tag_value="industry_trend")
            self.assertEqual(bogus_entity_hits, [])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
