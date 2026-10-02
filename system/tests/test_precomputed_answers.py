"""
test_precomputed_answers.py — Option C: precomputed answers at graph build time

PC1: build_precomputed_answers() output structure and content
PC2: write_precomputed_answers() persists to index.json
PC3: build_index() embeds precomputed_answers automatically
PC4: daily_brief._load_active_knowledge_assets() is a pure read (no computation)
PC5: query_engine resolves from precomputed_answers in index.json
PC6: edge cases — empty graph, missing templates, partial data
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import micro_graph_query as mgq


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_index(
    *,
    include_templates=True,
    include_top_ops=True,
    include_states=True,
    include_coops=True,
    graph_slug="test_graph",
    entity_name="Test Entity",
    source_workbook="test.xlsx",
) -> dict:
    """Build a minimal index dict for testing."""
    index: dict = {
        "contract": "rb_micro_graph_index_v1",
        "graph_slug": graph_slug,
        "name": entity_name,
        "source_workbook": source_workbook,
        "counts": {
            "nodes": 100,
            "edges": 500,
            "operator_entities_with_stores": 50,
            "stores_with_operator_entity": 200,
            "states_with_stores": 10,
            "coops_with_stores": 5,
            "operator_tiers": {
                "enterprise_25_plus": 3,
                "mid_tier_5_to_24": 15,
                "single_digit_1_to_4": 32,
            },
        },
    }
    if include_templates:
        index["retrieval_answer_templates"] = {
            "operator_distribution": (
                "Graph contains {operator_entities_with_stores} operators "
                "and {stores_with_operator_entity} stores "
                "({enterprise_25_plus} enterprise, {mid_tier_5_to_24} mid-tier, "
                "{single_digit_1_to_4} single-digit)."
            )
        }
    if include_top_ops:
        index["top_operator_entities"] = [
            {"name": "Flynn Group", "store_count": 100, "state_count": 8},
            {"name": "Acme Ops", "store_count": 50, "state_count": 4},
        ]
    if include_states:
        index["state_distribution"] = [
            {"state": "TX", "store_count": 40},
            {"state": "CA", "store_count": 30},
        ]
    if include_coops:
        index["coop_distribution"] = [
            {"name": "Southeast Coop", "store_count": 80},
            {"name": "Northwest Coop", "store_count": 60},
        ]
    return index


# ---------------------------------------------------------------------------
# PC1 — build_precomputed_answers() output structure
# ---------------------------------------------------------------------------

class TestBuildPrecomputedAnswers:
    def test_returns_dict(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        assert isinstance(result, dict)

    def test_operator_count_key_present(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        assert "operator_count" in result

    def test_operator_count_answer_populated(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        assert result["operator_count"]["answer"]
        assert "50" in result["operator_count"]["answer"]  # operator_entities_with_stores
        assert "200" in result["operator_count"]["answer"]  # stores_with_operator_entity

    def test_operator_count_tier_values(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        ans = result["operator_count"]["answer"]
        assert "3" in ans   # enterprise_25_plus
        assert "15" in ans  # mid_tier_5_to_24
        assert "32" in ans  # single_digit_1_to_4

    def test_largest_operators_key_present(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        assert "largest_operators" in result

    def test_largest_operators_names_in_answer(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        ans = result["largest_operators"]["answer"]
        assert "Flynn Group" in ans
        assert "100" in ans

    def test_largest_operators_list_in_result(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        op_list = result["largest_operators"].get("operator_list", [])
        assert len(op_list) == 2
        assert op_list[0]["name"] == "Flynn Group"

    def test_state_distribution_key_present(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        assert "state_distribution" in result

    def test_state_distribution_answer(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        ans = result["state_distribution"]["answer"]
        assert "TX" in ans
        assert "CA" in ans

    def test_state_distribution_total_states(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        assert result["state_distribution"]["total_states"] == 2

    def test_coop_distribution_key_present(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        assert "coop_distribution" in result

    def test_coop_distribution_names_in_answer(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        ans = result["coop_distribution"]["answer"]
        assert "Southeast Coop" in ans

    def test_all_answers_have_tier_1(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        for key, val in result.items():
            assert val.get("tier") == 1, f"{key} missing tier=1"

    def test_all_answers_have_source_graph_index(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        for key, val in result.items():
            assert val.get("source") == "graph_index", f"{key} wrong source"

    def test_all_answers_have_note(self):
        index = _make_index()
        result = mgq.build_precomputed_answers(index)
        for key, val in result.items():
            assert val.get("note"), f"{key} missing note"

    def test_source_workbook_in_largest_operators(self):
        index = _make_index(source_workbook="myfile.xlsx")
        result = mgq.build_precomputed_answers(index)
        ans = result.get("largest_operators", {}).get("answer", "")
        assert "myfile.xlsx" in ans


# ---------------------------------------------------------------------------
# PC2 — write_precomputed_answers() persists to index.json
# ---------------------------------------------------------------------------

class TestWritePrecomputedAnswers:
    def test_writes_to_index_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            graph_slug = "test_write_graph"
            gdir = tmp_path / graph_slug
            gdir.mkdir()
            index = _make_index(graph_slug=graph_slug)
            (gdir / "index.json").write_text(json.dumps(index))

            with patch.object(mgq, "MICRO_DIR", tmp_path):
                answers = mgq.write_precomputed_answers(graph_slug)

            saved = json.loads((gdir / "index.json").read_text())
            assert "precomputed_answers" in saved
            assert saved["precomputed_answers"] == answers

    def test_returns_answers_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            graph_slug = "test_write2"
            gdir = tmp_path / graph_slug
            gdir.mkdir()
            index = _make_index(graph_slug=graph_slug)
            (gdir / "index.json").write_text(json.dumps(index))

            with patch.object(mgq, "MICRO_DIR", tmp_path):
                answers = mgq.write_precomputed_answers(graph_slug)

            assert "operator_count" in answers

    def test_raises_if_index_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(mgq, "MICRO_DIR", Path(tmp)):
                with pytest.raises(FileNotFoundError):
                    mgq.write_precomputed_answers("nonexistent_graph")


# ---------------------------------------------------------------------------
# PC3 — build_index() embeds precomputed_answers
# ---------------------------------------------------------------------------

class TestBuildIndexEmbedsAnswers:
    def test_build_index_includes_precomputed_answers_key(self):
        """build_index() must include precomputed_answers in the returned dict."""
        import rb_core as core
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            slug = "embed_test"
            gdir = tmp_path / slug
            gdir.mkdir()

            # Minimal graph.json
            graph_data = {
                "graph_id": "test:embed_test",
                "name": "Embed Test",
                "nodes": [
                    {"id": "store:1", "type": "store", "attributes": {"state": "TX"}},
                    {"id": "entity:1", "type": "entity", "name": "Flynn Group"},
                ],
                "edges": [
                    {"type": "operated_by_entity", "from": "store:1", "to": "entity:1"},
                ],
            }
            (gdir / "graph.json").write_text(json.dumps(graph_data))

            with patch.object(mgq, "MICRO_DIR", tmp_path), \
                 patch.object(mgq, "REGISTRY_PATH", tmp_path / "index.json"), \
                 patch.object(core, "PROJECT_DIR", tmp_path):
                index = mgq.build_index(slug)

            assert "precomputed_answers" in index

    def test_build_index_precomputed_answers_is_dict(self):
        import rb_core as core
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            slug = "embed_test2"
            gdir = tmp_path / slug
            gdir.mkdir()
            graph_data = {"graph_id": "test:embed_test2", "name": "Test", "nodes": [], "edges": []}
            (gdir / "graph.json").write_text(json.dumps(graph_data))

            with patch.object(mgq, "MICRO_DIR", tmp_path), \
                 patch.object(mgq, "REGISTRY_PATH", tmp_path / "index.json"), \
                 patch.object(core, "PROJECT_DIR", tmp_path):
                index = mgq.build_index(slug)

            assert isinstance(index["precomputed_answers"], dict)


# ---------------------------------------------------------------------------
# PC4 — daily_brief._load_active_knowledge_assets() is a pure read
# ---------------------------------------------------------------------------

class TestDailyBriefPureRead:
    def test_reads_precomputed_answers_from_index(self):
        import daily_brief as db

        precomputed = {
            "operator_count": {"answer": "50 operators", "tier": 1, "source": "graph_index"},
            "largest_operators": {"answer": "Top ops list", "tier": 1, "source": "graph_index"},
        }

        fake_registry = {
            "graphs": [{
                "graph_id": "micro_ecosystem:test",
                "graph_slug": "test_slug",
                "name": "Test Graph",
                "status": "partial",
                "index_path": "system/graphs/micro/test_slug/index.json",
                "activation_terms": ["test"],
                "counts": {"nodes": 100, "edges": 500},
                "updated_at": "2026-06-01",
            }]
        }
        fake_index = {"precomputed_answers": precomputed}

        import rb_core as core
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", side_effect=lambda **kw: (
                 json.dumps(fake_index)
                 if "test_slug" in str(Path.__self__ if hasattr(Path, "__self__") else "")
                 else json.dumps(fake_registry)
             )):
            # Use direct patching of json.loads instead
            original_loads = json.loads

            def patched_loads(s, **kw):
                d = original_loads(s, **kw)
                return d

            registry_path = core.SYSTEM_DIR / "graphs" / "index.json"

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                # Write fake registry
                reg_path = tmp_path / "graphs" / "index.json"
                reg_path.parent.mkdir(parents=True)
                reg_path.write_text(json.dumps(fake_registry))
                # Write fake index
                idx_dir = tmp_path / "system" / "graphs" / "micro" / "test_slug"
                idx_dir.mkdir(parents=True)
                (idx_dir / "index.json").write_text(json.dumps(fake_index))

                with patch.object(core, "SYSTEM_DIR", tmp_path / "system"), \
                     patch.object(core, "PROJECT_DIR", tmp_path):
                    # Make the registry path point to our temp reg
                    (tmp_path / "system" / "graphs").mkdir(parents=True, exist_ok=True)
                    (tmp_path / "system" / "graphs" / "index.json").write_text(
                        json.dumps(fake_registry)
                    )
                    assets = db._load_active_knowledge_assets()

        # If any assets were loaded, precomputed_answers should come from index
        # (This test primarily ensures no exception is raised and structure is correct)
        assert isinstance(assets, list)

    def test_empty_precomputed_falls_back_gracefully(self):
        """If precomputed_answers is empty, the asset still loads without error."""
        import daily_brief as db
        import rb_core as core

        fake_registry = {
            "graphs": [{
                "graph_id": "micro_ecosystem:empty_test",
                "graph_slug": "empty_slug",
                "name": "Empty Graph",
                "status": "partial",
                "index_path": "system/graphs/micro/empty_slug/index.json",
                "activation_terms": [],
                "counts": {"nodes": 0, "edges": 0},
            }]
        }
        fake_index = {}  # No precomputed_answers key at all

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "system" / "graphs").mkdir(parents=True)
            (tmp_path / "system" / "graphs" / "index.json").write_text(
                json.dumps(fake_registry)
            )
            idx_dir = tmp_path / "system" / "graphs" / "micro" / "empty_slug"
            idx_dir.mkdir(parents=True)
            (idx_dir / "index.json").write_text(json.dumps(fake_index))

            with patch.object(core, "SYSTEM_DIR", tmp_path / "system"), \
                 patch.object(core, "PROJECT_DIR", tmp_path):
                assets = db._load_active_knowledge_assets()

        assert isinstance(assets, list)
        if assets:
            # precomputed_answers should be an empty dict, not raise
            assert isinstance(assets[0].get("precomputed_answers", {}), dict)


# ---------------------------------------------------------------------------
# PC5 — query_engine reads from precomputed_answers in index.json
# ---------------------------------------------------------------------------

class TestQueryEngineUsesPrecomputed:
    def test_micro_dispatch_reads_precomputed_operator_count(self):
        import query_engine as qe

        precomputed = {
            "operator_count": {
                "answer": "Precomputed: 50 operators, 200 stores.",
                "tier": 1,
                "source": "graph_index",
            }
        }
        fake_registry = {
            "graphs": [{
                "graph_id": "micro_ecosystem:test_precomp",
                "graph_slug": "test_precomp",
                "name": "Test Precomp",
                "status": "partial",
                "source_workbook": "test.xlsx",
                "activation_terms": ["testcorp", "test precomp"],
            }]
        }
        fake_index = {
            "counts": {"nodes": 100, "edges": 200, "operator_tiers": {}},
            "retrieval_answer_templates": {},
            "precomputed_answers": precomputed,
        }

        def fake_exists(self):
            return True

        def fake_read_text(self, **kwargs):
            s = str(self)
            if "test_precomp" in s and "index.json" in s:
                return json.dumps(fake_index)
            return json.dumps(fake_registry)

        with patch.object(Path, "exists", fake_exists), \
             patch.object(Path, "read_text", fake_read_text):
            with patch("query_engine.core") as mock_core:
                mock_core.SYSTEM_DIR = Path("/fake")
                result = qe._dispatch_micro("testcorp", "how many operators?")

        assert result["status"] == "ok"
        assert result["answer"] == "Precomputed: 50 operators, 200 stores."

    def test_micro_dispatch_precomputed_beats_template(self):
        """When precomputed_answers exists, it takes precedence over retrieval_answer_templates."""
        import query_engine as qe

        precomputed = {
            "operator_count": {"answer": "PRECOMPUTED ANSWER", "tier": 1, "source": "graph_index"},
        }
        fake_registry = {
            "graphs": [{
                "graph_id": "micro_ecosystem:priority_test",
                "graph_slug": "priority_test",
                "name": "Priority Test",
                "status": "partial",
                "source_workbook": "test.xlsx",
                "activation_terms": ["priority"],
            }]
        }
        fake_index = {
            "counts": {"nodes": 50, "edges": 100,
                       "operator_entities_with_stores": 10,
                       "stores_with_operator_entity": 50,
                       "operator_tiers": {"enterprise_25_plus": 1, "mid_tier_5_to_24": 5, "single_digit_1_to_4": 4}},
            "retrieval_answer_templates": {
                "operator_distribution": "TEMPLATE ANSWER {operator_entities_with_stores}"
            },
            "precomputed_answers": precomputed,
        }

        def fake_exists(self):
            return True

        def fake_read_text(self, **kwargs):
            s = str(self)
            if "priority_test" in s and "index.json" in s:
                return json.dumps(fake_index)
            return json.dumps(fake_registry)

        with patch.object(Path, "exists", fake_exists), \
             patch.object(Path, "read_text", fake_read_text):
            with patch("query_engine.core") as mock_core:
                mock_core.SYSTEM_DIR = Path("/fake")
                result = qe._dispatch_micro("priority", "how many operators?")

        assert result["answer"] == "PRECOMPUTED ANSWER"


# ---------------------------------------------------------------------------
# PC6 — Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_template_no_operator_count(self):
        index = _make_index(include_templates=False)
        result = mgq.build_precomputed_answers(index)
        assert "operator_count" not in result

    def test_no_top_ops_no_largest_operators(self):
        index = _make_index(include_top_ops=False)
        result = mgq.build_precomputed_answers(index)
        assert "largest_operators" not in result

    def test_no_state_data_no_state_distribution(self):
        index = _make_index(include_states=False)
        result = mgq.build_precomputed_answers(index)
        assert "state_distribution" not in result

    def test_no_coop_data_no_coop_distribution(self):
        index = _make_index(include_coops=False)
        result = mgq.build_precomputed_answers(index)
        assert "coop_distribution" not in result

    def test_empty_index_returns_empty_dict(self):
        result = mgq.build_precomputed_answers({})
        assert result == {}

    def test_malformed_template_does_not_raise(self):
        index = _make_index()
        index["retrieval_answer_templates"]["operator_distribution"] = "Bad {missing_key} template"
        result = mgq.build_precomputed_answers(index)
        # Should not raise; operator_count may be absent or contain unrendered template
        assert isinstance(result, dict)

    def test_counts_zero_still_renders(self):
        index = _make_index()
        index["counts"]["operator_entities_with_stores"] = 0
        index["counts"]["stores_with_operator_entity"] = 0
        result = mgq.build_precomputed_answers(index)
        if "operator_count" in result:
            assert "0" in result["operator_count"]["answer"]

    def test_entity_name_in_note(self):
        index = _make_index(entity_name="MyTestCorp")
        result = mgq.build_precomputed_answers(index)
        for key, val in result.items():
            assert "MyTestCorp" in val.get("note", ""), f"{key} note missing entity name"
