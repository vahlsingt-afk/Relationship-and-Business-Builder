import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import top500_profile_gapfill_ingest as ingest


class GapfillIngestTest(unittest.TestCase):
    def test_detects_gapfill_schema(self):
        data = {"dataset": {"name": "RBB Top-500 Company Profile Gap Fill — Test", "records": []}}
        self.assertTrue(ingest.is_gapfill_dataset(data))
        self.assertTrue(ingest.classify_bytes(json.dumps(data).encode()))

    def test_detects_top600_gapfill_schema(self):
        data = {"dataset": {"name": "RBB Top-600 Company Profile Gap Fill — Test", "records": []}}
        self.assertTrue(ingest.is_gapfill_dataset(data))
        self.assertTrue(ingest.classify_bytes(json.dumps(data).encode()))

    def test_rejects_generic_json(self):
        self.assertFalse(ingest.is_gapfill_dataset({"dataset": {"records": []}}))
        self.assertFalse(ingest.classify_bytes(b'{"hello":"world"}'))

    def test_resolves_vendor_with_legal_suffix(self):
        vendor = {"id": "vendor-thanx", "name": "Thanx", "entity_type": "vendor", "aliases": []}
        graph = {"entities": [vendor], "relationships": []}
        self.assertIs(ingest._resolve_vendor("Thanx, Inc.", graph, {vendor["id"]: vendor}), vendor)

    def test_imports_each_record_without_article_subject_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            graph = {
                "entities": [
                    {"id": "brand-alpha", "name": "Alpha", "entity_type": "brand", "attributes": {}, "aliases": []},
                    {"id": "vendor-toast", "name": "Toast", "entity_type": "vendor", "attributes": {}, "aliases": []},
                ],
                "relationships": [], "sources": [],
            }
            payload = {
                "dataset": {"name": "RBB Top-500 Company Profile Gap Fill", "version": "v1", "records": [{
                    "brand_id": "brand-alpha", "brand_name": "Alpha", "company_profile": {
                        "parent_ownership": {"value": "Alpha Holdings", "confidence": 95},
                        "synopsis": {"value": "Alpha profile", "confidence": 90},
                        "existing_technology_relationships": [{"vendor": "Toast", "category": "pos"}],
                    },
                }]},
            }
            source = tmp_path / "packet.json"
            source.write_text(json.dumps(payload))
            with patch.object(ingest.ei, "_read_graph", return_value=graph), \
                 patch.object(ingest.ei, "_write_graph"), \
                 patch.object(ingest.bpc, "ROOT", tmp_path / "profiles"), \
                 patch.object(ingest, "RECEIPT_PATH", tmp_path / "receipt.json"):
                result = ingest.ingest(source)
            self.assertEqual(result["counts"]["brands_resolved"], 1)
            self.assertEqual(result["counts"]["relationships_added"], 1)
            profile = json.loads((tmp_path / "profiles" / "brand-alpha.json").read_text())
            self.assertEqual(profile["identity"]["parent_ownership"]["value"], "Alpha Holdings")
            self.assertEqual(graph["relationships"][0]["from_entity_id"], "brand-alpha")


if __name__ == "__main__":
    unittest.main()
