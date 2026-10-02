from __future__ import annotations

from system.scripts import competitor_intelligence_common as common


def test_product_line_declaration_is_direct_competition():
    assert common.competitive_relationship_class({"competes_on": ["payments"]}) == "product_line_competitor"


def test_known_category_without_overlap_is_adjacent():
    assert common.competitive_relationship_class({"primary_category": "digital_signage"}) == "adjacent_ecosystem_vendor"


def test_empty_trade_show_shell_remains_unclassified():
    assert common.competitive_relationship_class({"display_name": "Example Vendor"}) == "unclassified_vendor"
