"""
test_morning_pipeline_mutation_policy_reconciliation.py

Handoff acceptance test 11: "Mutation totals reconcile exactly across
execution report, audit receipt, and brief." Root cause this closes: the
KFC watchlist discrepancy (auto_applied:true, records_changed:0, empty
mutation_lifecycle) happened because there was no single source of truth a
mutation's "did this actually apply" status was written to that both the
execution report AND an independent audit surface both read. mutation_
policy.py's receipts are now that shared source (see intelligence_
assessment.py's watchlist_add auto-apply path and passive_ri_ingest.py's
LinkedIn auto-confirm path, both of which call mutation_policy.record_
receipt()); morning_pipeline.build_execution_report() folds
mutation_policy.summarize_receipts() into both records_changed and
mutations_generated AND exposes the same summary verbatim under
intelligence_cycle_statistics.daily_monitoring.mutation_policy_reconciliation
-- this test proves those two views of the same receipts agree exactly
rather than drifting apart the way records_changed and the old (unrelated)
mutation_lifecycle field could.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import morning_pipeline  # noqa: E402
import mutation_policy as mp  # noqa: E402


def test_mutation_policy_receipts_reconcile_across_execution_report(tmp_path: Path, monkeypatch) -> None:
    system_dir = tmp_path / "system"
    published_dir = system_dir / "published" / "daily"
    receipts_path = tmp_path / "mutation_policy_receipts.jsonl"
    monkeypatch.setattr(morning_pipeline, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(morning_pipeline, "PUBLISHED_DIR", published_dir)
    monkeypatch.setattr(mp, "RECEIPTS_PATH", receipts_path)
    (system_dir / ".cache").mkdir(parents=True, exist_ok=True)
    (system_dir / ".cache" / "source_health.json").write_text("{}", encoding="utf-8")

    run_date = "2026-09-18"

    # Two auto-applied mutations (e.g. one watchlist_add, one LinkedIn
    # dated-successor touch), one requiring confirmation, one rejected as a
    # duplicate -- a realistic mixed batch for one day's receipts.
    decisions_and_applied = [
        (mp.decide(source="intelligence_assessment:watchlist_add", new_value="brand-kfc",
                    existing_value=None, is_set_member=True), True, "ecosystem_intelligence.json:watch_list"),
        (mp.decide(source="passive_ri_ingest:linkedin_messaging", new_value="2026-09-16",
                    existing_value="2026-08-30", new_date="2026-09-16", existing_date="2026-08-30"),
         True, "ri_events:ri_2026-09-16-002"),
        (mp.decide(source="manual_relationship_intake", new_value="VP Sales",
                    existing_value="Director", is_replacement=True), False, None),
        (mp.decide(source="email", new_value="Acme Corp", existing_value="Acme Corp"), False, None),
    ]
    for decision, applied, artifact in decisions_and_applied:
        mp.record_receipt(decision, artifact=artifact, applied=applied, path=receipts_path)

    # record_receipt stamps recorded_at with the real current time; the
    # execution report filters by exact run_date, so backdate/forward-date
    # nothing here is needed as long as the test runs "today" in UTC terms --
    # instead, directly verify against whatever date record_receipt actually
    # used, by reading the receipts back rather than assuming "today".
    written = mp.load_receipts(path=receipts_path)
    actual_run_date = written[0]["recorded_at"][:10]

    report = morning_pipeline.build_execution_report({
        "generated_at": f"{actual_run_date}T10:00:00+00:00",
        "date": actual_run_date,
        "mode": "full",
        "ok": True,
        "steps": [],
    })

    expected_summary = mp.summarize_receipts(written)

    # 1. The reconciliation block exposed on the execution report is exactly
    #    what mutation_policy itself computes from the same receipts.
    daily_monitoring = report["intelligence_cycle_statistics"]["daily_monitoring"]
    assert daily_monitoring["mutation_policy_reconciliation"] == expected_summary

    # 2. records_changed / mutations_generated (the two top-level fields
    #    every other consumer -- daily_brief.py, the KFC-style dashboards --
    #    reads) include exactly this batch's automatically_applied /
    #    mutations_generated contribution, not a silently different number.
    assert expected_summary["automatically_applied"] == 2
    assert expected_summary["mutations_generated"] == 4
    assert report["records_changed"] == expected_summary["automatically_applied"]
    assert report["mutations_generated"] == expected_summary["mutations_generated"]
    assert report["cycle_proof"]["record"]["records_changed"] == report["records_changed"]
