from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import deep_research_coverage as coverage  # noqa: E402


def test_initial_then_maintenance_plan(tmp_path, monkeypatch):
    graph = {
        "entities": [
            {"id": "brand-a", "name": "A", "entity_type": "brand", "attributes": {"rank": 1, "unit_count": 101}},
            {"id": "brand-b", "name": "B", "entity_type": "brand", "attributes": {"rank": 2, "unit_count": 80}},
            {"id": "brand-out", "name": "Out", "entity_type": "brand", "attributes": {"rank": 1501}},
        ]
    }
    registry = {"registry": [{"competitor_slug": "toast", "display_name": "Toast"}]}
    graph_path = tmp_path / "graph.json"
    registry_path = tmp_path / "registry.json"
    state_path = tmp_path / "state.json"
    graph_path.write_text(json.dumps(graph))
    registry_path.write_text(json.dumps(registry))
    monkeypatch.setattr(coverage, "GRAPH_PATH", graph_path)
    monkeypatch.setattr(coverage, "COMPETITOR_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(coverage, "STATE_PATH", state_path)

    first = coverage.plan(2)
    assert first["phase"] == "initial_coverage"
    assert [row["name"] for row in first["targets"]] == ["A", "B"]

    keys = list(json.loads(state_path.read_text())["targets"])
    coverage.record("packet.md", keys)
    second = coverage.plan(5)
    assert second["phase"] == "maintenance"
    assert second["targets"] == []


def test_brand_plan_converges_to_95_5_and_prioritizes_70_plus_secondary(tmp_path, monkeypatch):
    entities = []
    for index in range(1, 22):
        entities.append(
            {
                "id": f"enterprise-{index}",
                "name": f"Enterprise {index:02d}",
                "entity_type": "brand",
                "attributes": {"rank": index, "unit_count": 101 + index},
            }
        )
    entities.extend(
        [
            {
                "id": "secondary-80",
                "name": "Secondary 80",
                "entity_type": "brand",
                "attributes": {"rank": 500, "unit_count": 80},
            },
            {
                "id": "secondary-20",
                "name": "Secondary 20",
                "entity_type": "brand",
                "attributes": {"rank": 100, "unit_count": 20},
            },
        ]
    )
    _isolate_universe(tmp_path, monkeypatch, entities=entities)

    selected = []
    for batch_index in range(4):
        result = coverage.plan(5)
        batch = result["targets"]
        selected.extend(batch)
        coverage.record(f"packet-{batch_index}.md", [f"company:{row['entity_id']}" for row in batch])

    secondary = [row for row in selected if row["research_priority"] == "secondary_5"]
    assert len(secondary) == 1
    assert secondary[0]["name"] == "Secondary 80"
    assert secondary[0]["secondary_band"] == "70_to_100_locations"
    assert sum(row["research_priority"] == "primary_95" for row in selected) == 19


def test_explicit_emerging_brand_is_primary_even_below_100_units(tmp_path, monkeypatch):
    _isolate_universe(
        tmp_path,
        monkeypatch,
        entities=[
            {
                "id": "emerging",
                "name": "Emerging",
                "entity_type": "brand",
                "attributes": {"rank": 10, "unit_count": 25, "emerging": True},
            },
            {
                "id": "secondary",
                "name": "Secondary",
                "entity_type": "brand",
                "attributes": {"rank": 1, "unit_count": 90},
            },
        ],
    )
    result = coverage.plan(1)
    assert result["targets"][0]["name"] == "Emerging"
    assert result["targets"][0]["research_priority"] == "primary_95"


def test_real_universe_contains_ranked_companies_and_competitors(tmp_path, monkeypatch):
    monkeypatch.setattr(coverage, "STATE_PATH", tmp_path / "state.json")
    result = coverage.plan(1)
    assert result["counts"]["companies_total"] > 1000
    assert result["counts"]["competitors_total"] > 0


def _isolate_universe(tmp_path, monkeypatch, *, entities=None, registry=None):
    graph_path = tmp_path / "graph.json"
    registry_path = tmp_path / "registry.json"
    graph_path.write_text(json.dumps({"entities": entities or []}))
    registry_path.write_text(json.dumps({"registry": registry or []}))
    monkeypatch.setattr(coverage, "GRAPH_PATH", graph_path)
    monkeypatch.setattr(coverage, "COMPETITOR_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(coverage, "STATE_PATH", tmp_path / "state.json")


def test_record_outcome_updates_target_and_source_domain_stats(tmp_path, monkeypatch):
    _isolate_universe(
        tmp_path,
        monkeypatch,
        entities=[{"id": "brand-a", "name": "A", "entity_type": "brand", "attributes": {"rank": 1}}],
        registry=[{"competitor_slug": "toast", "display_name": "Toast"}],
    )
    coverage.sync()
    source_ledger = [
        {"url": "https://www.toasttab.com/case-studies/x", "source_type": "case study", "productive": True},
        {"url": "https://toasttab.com/blog/y", "source_type": "blog", "productive": False},
        {"url": "https://example.com/z", "source_type": "logo wall", "productive": False},
    ]

    outcome = coverage.record_outcome(
        "dr-20260920-1000-toast", ["competitor:toast", "company:brand-a"], source_ledger
    )

    assert outcome["productive_pages"] == 1
    assert outcome["unproductive_pages"] == 2
    assert set(outcome["targets_scored"]) == {"competitor:toast", "company:brand-a"}

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["targets"]["competitor:toast"]["productive_pages"] == 1
    assert state["targets"]["competitor:toast"]["unproductive_pages"] == 2
    assert state["targets"]["company:brand-a"]["productive_pages"] == 1
    assert state["targets"]["company:brand-a"]["last_outcome_packet"] == "dr-20260920-1000-toast"

    # www. and bare-host variants of the same source must fold into one domain
    sources = state["sources"]
    assert sources["toasttab.com"] == {
        "checked": 2,
        "productive": 1,
        "unproductive": 1,
        "last_checked_at": sources["toasttab.com"]["last_checked_at"],
    }
    assert sources["example.com"]["checked"] == 1
    assert sources["example.com"]["unproductive"] == 1


def test_record_outcome_skips_unknown_target_keys(tmp_path, monkeypatch):
    _isolate_universe(tmp_path, monkeypatch)
    outcome = coverage.record_outcome(
        "dr-x", ["competitor:does-not-exist"], [{"url": "https://a.com", "productive": True}]
    )
    assert outcome["targets_scored"] == []


def test_record_outcome_dry_run_persists_nothing(tmp_path, monkeypatch):
    _isolate_universe(
        tmp_path,
        monkeypatch,
        entities=[{"id": "brand-a", "name": "A", "entity_type": "brand", "attributes": {"rank": 1}}],
    )
    coverage.sync()
    coverage.record_outcome(
        "dr-x", ["company:brand-a"], [{"url": "https://a.com", "productive": True}], dry_run=True
    )
    state = json.loads((tmp_path / "state.json").read_text())
    assert "productive_pages" not in state["targets"]["company:brand-a"]
    assert "sources" not in state


def _write_sidecar(drop_dir, name, packet_id, targets, source_ledger):
    (drop_dir / name).write_text(
        json.dumps({"packet_id": packet_id, "targets": targets, "source_ledger": source_ledger})
    )


def test_sweep_sidecars_processes_new_files_and_dedupes_on_rerun(tmp_path, monkeypatch):
    _isolate_universe(
        tmp_path,
        monkeypatch,
        entities=[{"id": "brand-a", "name": "A", "entity_type": "brand", "attributes": {"rank": 1}}],
    )
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir()
    monkeypatch.setattr(coverage, "DROP_DIR", drop_dir)
    monkeypatch.setattr(coverage, "SIDECAR_MANIFEST_PATH", tmp_path / "sidecar_manifest.json")
    coverage.sync()
    _write_sidecar(
        drop_dir,
        "2026-09-20_1000_a_deep-research.json",
        "dr-20260920-1000-a",
        ["company:brand-a"],
        [{"url": "https://a.com/x", "productive": True}],
    )

    first = coverage.sweep_sidecars()
    assert len(first) == 1
    assert first[0]["ok"] is True
    assert first[0]["productive_pages"] == 1

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["targets"]["company:brand-a"]["productive_pages"] == 1

    # a second sweep with no new sidecars must not re-record the same packet
    second = coverage.sweep_sidecars()
    assert second == []
    state_again = json.loads((tmp_path / "state.json").read_text())
    assert state_again["targets"]["company:brand-a"]["productive_pages"] == 1


def test_sweep_sidecars_handles_malformed_sidecar_without_retrying(tmp_path, monkeypatch):
    _isolate_universe(tmp_path, monkeypatch)
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir()
    monkeypatch.setattr(coverage, "DROP_DIR", drop_dir)
    monkeypatch.setattr(coverage, "SIDECAR_MANIFEST_PATH", tmp_path / "sidecar_manifest.json")
    (drop_dir / "broken_deep-research.json").write_text("{not valid json")

    first = coverage.sweep_sidecars()
    assert len(first) == 1
    assert first[0]["ok"] is False

    # a permanently-broken file must be marked processed, not retried forever
    second = coverage.sweep_sidecars()
    assert second == []


def test_sweep_sidecars_missing_drop_dir_returns_empty(tmp_path, monkeypatch):
    _isolate_universe(tmp_path, monkeypatch)
    monkeypatch.setattr(coverage, "DROP_DIR", tmp_path / "does-not-exist")
    monkeypatch.setattr(coverage, "SIDECAR_MANIFEST_PATH", tmp_path / "sidecar_manifest.json")
    assert coverage.sweep_sidecars() == []


def test_sweep_sidecars_dry_run_does_not_persist_manifest_or_state(tmp_path, monkeypatch):
    _isolate_universe(
        tmp_path,
        monkeypatch,
        entities=[{"id": "brand-a", "name": "A", "entity_type": "brand", "attributes": {"rank": 1}}],
    )
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir()
    manifest_path = tmp_path / "sidecar_manifest.json"
    monkeypatch.setattr(coverage, "DROP_DIR", drop_dir)
    monkeypatch.setattr(coverage, "SIDECAR_MANIFEST_PATH", manifest_path)
    coverage.sync()
    _write_sidecar(
        drop_dir,
        "packet_deep-research.json",
        "dr-x",
        ["company:brand-a"],
        [{"url": "https://a.com/x", "productive": True}],
    )

    coverage.sweep_sidecars(dry_run=True)

    assert not manifest_path.exists()
    state = json.loads((tmp_path / "state.json").read_text())
    assert "productive_pages" not in state["targets"]["company:brand-a"]

    # nothing was marked processed, so a real (non-dry-run) sweep afterward
    # must still pick the same file up
    real = coverage.sweep_sidecars()
    assert len(real) == 1
    assert real[0]["ok"] is True
