import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_competitor_category_gapfill as ingest


class CompetitorCategoryGapfillTest(unittest.TestCase):
    def test_classifies_packet(self):
        packet = {"artifact_type": "rbb_competitor_category_gapfill", "competitors": []}
        self.assertTrue(ingest.classify_bytes(json.dumps(packet).encode()))

    def test_dry_run_maps_categories_and_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "competitors" / "vendor-a"
            folder.mkdir(parents=True)
            profile = {"competitor_slug": "vendor-a", "display_name": "Vendor A", "aliases": [], "competes_on": [], "products": []}
            (folder / "competitor.json").write_text(json.dumps(profile))
            packet = {"artifact_type": "rbb_competitor_category_gapfill", "packet_id": "p1", "competitors": [{
                "competitor_slug": "vendor-a", "display_name": "Vendor A", "genius_competes_on": ["pos"],
                "restaurant_tech_categories": [{"category": "pos", "confidence": 95, "sources": []}],
                "products": [{"name": "A POS", "categories": ["pos"], "confidence": 95, "sources": []}],
            }]}
            source = root / "packet.json"; source.write_text(json.dumps(packet))
            with patch.object(ingest.cic, "ROOT", root), patch.object(ingest, "RECEIPT_PATH", root / "receipt.json"):
                result = ingest.ingest(source, dry_run=True)
            self.assertEqual(result["counts"]["competitors_resolved"], 1)
            self.assertEqual(result["counts"]["category_sets_changed"], 1)
            self.assertEqual(json.loads((folder / "competitor.json").read_text())["competes_on"], [])


if __name__ == "__main__":
    unittest.main()
