from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import intelligence_coverage_matrix as matrix


def test_brand_and_vendor_dimension_sets_cover_discovery_signals():
    assert {"procurement_rfp", "hiring", "contract_timing", "buying_triggers"}.issubset(matrix.BRAND_DIMENSIONS)
    assert {"financial_health", "customer_exposure", "implementation_risk"}.issubset(matrix.VENDOR_DIMENSIONS)
