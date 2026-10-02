"""
test_loop_reconciliation.py

RB-2026-08-23 (P1-4): CANONICAL_REGISTRY.yaml's execution_loops.conflict_policy
has said since 2026-08-19 that "one intent must not exist as active in both
namespaces," with nothing enforcing it. This is that enforcement -- detection
only, per the registry's own consolidation_target being an unresolved
"explicit_single_loop_authority_decision," not something a script should
guess at. Never auto-merges or auto-closes; only flags for Todd.

Fixtures use synthetic known-duplicate-shaped and known-distinct pairs,
not the real loop_ledger.md/eolms/loops.json (already verified separately
against live data: 11 open L-, 19 open EL-, 0 flagged at the real threshold).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402
import loop_reconciliation as lr  # noqa: E402


def _write_ledger(path: Path, rows: list[tuple[str, str, str, str, str, str]]) -> None:
    lines = ["# Loop Ledger", "", "| ID | Opened | Person/Company | Loop | Closure target | Status |",
             "|---|---|---|---|---|---|"]
    for loop_id, opened, party, desc, target, status in rows:
        lines.append(f"| {loop_id} | {opened} | {party} | {desc} | {target} | {status} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_eolms(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    defaults = {
        "category": "action", "status": "active", "priority": "medium",
        "strategic_value": None, "owner": "Todd Vahlsing",
        "created_at": "2026-08-01", "updated_at": "2026-08-01", "last_activity": "2026-08-01",
        "next_action": None, "waiting_on": None, "activation_date": None,
        "activation_condition": None, "due_date": None, "cadence_days": None,
        "related_people": [], "related_orgs": [], "related_loop_ids": [],
        "blocked_by": [], "related_documents": [], "source_ref": None,
        "confidence": None, "history": [], "tags": [], "requires_verification": False,
    }
    path.write_text(json.dumps([{**defaults, **r} for r in records], indent=2), encoding="utf-8")


def _setup(tmp_path: Path, monkeypatch, *, ledger_rows, eolms_records):
    ledger_path = tmp_path / "loop_ledger.md"
    eolms_path = tmp_path / "loops.json"
    _write_ledger(ledger_path, ledger_rows)
    _write_eolms(eolms_path, eolms_records)
    monkeypatch.setattr(core, "EOLMS_PATH", eolms_path)
    return ledger_path


def test_no_flags_when_loops_are_genuinely_distinct(tmp_path, monkeypatch):
    ledger_path = _setup(
        tmp_path, monkeypatch,
        ledger_rows=[("L-2026-08-01-001", "2026-08-01", "Jose Torres",
                      "Reach out about the retainer contract terms", "2026-09-01", "open")],
        eolms_records=[{"id": "EL-2026-08-01-001", "title": "Bathroom Remodel — get contractor quotes"}],
    )
    report = lr.build_report(ledger_path=ledger_path)
    assert report["possible_duplicate_intent"] == []
    assert report["open_l_count"] == 1
    assert report["open_el_count"] == 1


def test_flags_similar_unlinked_loops_as_possible_duplicate(tmp_path, monkeypatch):
    ledger_path = _setup(
        tmp_path, monkeypatch,
        ledger_rows=[("L-2026-08-01-001", "2026-08-01", "Ryan Hildebrand",
                      "Follow up with Ryan Hildebrand on the Worldpay Genius enterprise development plan",
                      "2026-09-01", "open")],
        eolms_records=[{
            "id": "EL-2026-08-01-001",
            "title": "Worldpay Genius enterprise development plan — Ryan Hildebrand follow-up",
            "related_orgs": ["Worldpay"],
            "related_loop_ids": [],  # deliberately NOT linked -- this is the case to catch
        }],
    )
    report = lr.build_report(ledger_path=ledger_path)
    assert len(report["possible_duplicate_intent"]) == 1
    flagged = report["possible_duplicate_intent"][0]
    assert flagged["l_id"] == "L-2026-08-01-001"
    assert flagged["el_id"] == "EL-2026-08-01-001"
    assert flagged["similarity_score"] >= lr.SIMILARITY_THRESHOLD


def test_known_linked_pair_is_not_flagged_even_though_similar(tmp_path, monkeypatch):
    """The whole point of related_loop_ids: this pair is deliberately,
    already cross-referenced -- it's known, not a conflict to adjudicate."""
    ledger_path = _setup(
        tmp_path, monkeypatch,
        ledger_rows=[("L-2026-08-01-001", "2026-08-01", "Ryan Hildebrand",
                      "Follow up with Ryan Hildebrand on the Worldpay Genius enterprise development plan",
                      "2026-09-01", "open")],
        eolms_records=[{
            "id": "EL-2026-08-01-001",
            "title": "Worldpay Genius enterprise development plan — Ryan Hildebrand follow-up",
            "related_orgs": ["Worldpay"],
            "related_loop_ids": ["L-2026-08-01-001"],
        }],
    )
    report = lr.build_report(ledger_path=ledger_path)
    assert report["possible_duplicate_intent"] == []
    assert "L-2026-08-01-001" in report["known_linked_l_ids"]


def test_closed_loops_excluded_from_both_namespaces(tmp_path, monkeypatch):
    ledger_path = _setup(
        tmp_path, monkeypatch,
        ledger_rows=[("L-2026-08-01-001", "2026-08-01", "Someone",
                      "Some closed matter", "2026-09-01", "**closed** — done")],
        eolms_records=[{"id": "EL-2026-08-01-001", "title": "Something", "status": "completed"}],
    )
    report = lr.build_report(ledger_path=ledger_path)
    assert report["open_l_count"] == 0
    assert report["open_el_count"] == 0


def test_distinct_obligation_count_dedupes_known_links(tmp_path, monkeypatch):
    ledger_path = _setup(
        tmp_path, monkeypatch,
        ledger_rows=[
            ("L-2026-08-01-001", "2026-08-01", "A", "matter a", "2026-09-01", "open"),
            ("L-2026-08-01-002", "2026-08-01", "B", "matter b", "2026-09-01", "open"),
        ],
        eolms_records=[
            {"id": "EL-2026-08-01-001", "title": "matter a el copy", "related_loop_ids": ["L-2026-08-01-001"]},
            {"id": "EL-2026-08-01-002", "title": "unrelated matter c"},
        ],
    )
    report = lr.build_report(ledger_path=ledger_path)
    # raw: 2 L- + 2 EL- = 4; one known link dedupes to 3 distinct
    assert report["raw_combined_count"] == 4
    assert report["distinct_obligation_count"] == 3


def test_never_auto_merges_or_mutates_source_files(tmp_path, monkeypatch):
    """This script is detection-only -- verify it doesn't touch either
    source file, since there's no safe automated way to pick a winner."""
    ledger_path = _setup(
        tmp_path, monkeypatch,
        ledger_rows=[("L-2026-08-01-001", "2026-08-01", "X", "y z alpha beta gamma", "2026-09-01", "open")],
        eolms_records=[{"id": "EL-2026-08-01-001", "title": "y z alpha beta gamma delta"}],
    )
    ledger_before = ledger_path.read_text()
    eolms_before = core.EOLMS_PATH.read_text()

    lr.build_report(ledger_path=ledger_path)

    assert ledger_path.read_text() == ledger_before
    assert core.EOLMS_PATH.read_text() == eolms_before


def test_real_repo_data_runs_cleanly_end_to_end():
    """Smoke test against the actual live loop_ledger.md/eolms/loops.json --
    just confirms the real parsers integrate without error and the counts
    are internally consistent, not asserting a specific conflict count
    (that would make this test brittle against normal data changes)."""
    report = lr.build_report()
    assert report["open_l_count"] >= 0
    assert report["open_el_count"] >= 0
    assert report["raw_combined_count"] == report["open_l_count"] + report["open_el_count"]
    assert report["distinct_obligation_count"] <= report["raw_combined_count"]
