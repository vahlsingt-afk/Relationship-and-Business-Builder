from pathlib import Path

from system.scripts import franchisee_hierarchy as fh


def test_operator_lookup_preserves_brand_unit_counts(tmp_path: Path):
    store = tmp_path / "hierarchy.json"
    store.write_text(
        '{"operators":[{"rank":1,"name":"Example Group","location":"Dallas, TX",'
        '"total_units":125,"brand_count":2,"hierarchy_level":"multi_brand_franchisee_group",'
        '"brands":[{"brand":"Taco Bell","unit_count":100},{"brand":"Wingstop","unit_count":25}]}]}'
    )
    result = fh.query("Example Group", path=store)
    assert result[0]["brands"][1] == {"brand": "Wingstop", "unit_count": 25}


def test_brand_reverse_lookup_and_multi_brand_filter(tmp_path: Path):
    store = tmp_path / "hierarchy.json"
    store.write_text(
        '{"operators":['
        '{"rank":1,"name":"Multi","total_units":30,"brand_count":2,"brands":[{"brand":"Wingstop","unit_count":10}]},'
        '{"rank":2,"name":"Single","total_units":20,"brand_count":1,"brands":[{"brand":"Wingstop","unit_count":20}]}'
        ']}'
    )
    assert [r["name"] for r in fh.query("Wingstop", path=store)] == ["Multi", "Single"]
    assert [r["name"] for r in fh.query(multi_brand_only=True, path=store)] == ["Multi"]
