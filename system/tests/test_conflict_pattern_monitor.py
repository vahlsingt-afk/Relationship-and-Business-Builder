"""
test_conflict_pattern_monitor.py — RB defect 2026-10-08, self-healing /
learning layer #2.

ecosystem_intelligence.py's check_relationship_conflict() already makes a
real, audited decision every time a claim rivals an existing one, logged
to system/inbox/ecosystem/conflict_queue.jsonl -- but nothing ever read
that log back. Confirmed live: the same Blaze Pizza POS rivalry (Qu vs.
Oracle) was independently re-confirmed as unresolved on 62 separate
pipeline runs across 13 days, invisible the whole time. conflict_pattern_
monitor.py aggregates the full decision history to surface exactly that
kind of pattern -- a recurring, never-escalated rivalry -- without ever
picking a winner itself.

These tests use small synthetic JSONL fixtures (never the real log) to
exercise the aggregation logic precisely.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import conflict_pattern_monitor as cpm  # noqa: E402


def _record(
    *, brand_id="brand-x", brand_name="Brand X", category="pos",
    resolution="recorded_alongside", detected_at="2026-09-01T00:00:00+00:00",
    incoming_vendor="vendor-a", incoming_name="Vendor A",
    existing_vendor="vendor-b", existing_name="Vendor B",
    note="",
) -> dict:
    return {
        "detected_at": detected_at, "brand_id": brand_id, "brand_name": brand_name,
        "category": category, "resolution": resolution,
        "incoming": {"vendor_id": incoming_vendor, "vendor_name": incoming_name},
        "existing": {"vendor_id": existing_vendor, "vendor_name": existing_name},
        "note": note,
    }


class TestLoadConflictLog(unittest.TestCase):
    def test_missing_file_returns_empty_list(self):
        self.assertEqual(cpm.load_conflict_log(Path("/tmp/does-not-exist-12345.jsonl")), [])

    def test_parses_real_lines_skips_malformed(self):
        tmp = Path(tempfile.mkdtemp()) / "log.jsonl"
        tmp.write_text(
            json.dumps(_record()) + "\n" + "not valid json\n" + "\n" + json.dumps(_record(brand_id="brand-y")) + "\n",
            encoding="utf-8",
        )
        records = cpm.load_conflict_log(tmp)
        self.assertEqual(len(records), 2)


class TestParseDt(unittest.TestCase):
    def test_naive_and_aware_timestamps_both_parse_and_compare(self):
        naive = cpm._parse_dt("2026-08-03T13:30:03")
        aware = cpm._parse_dt("2026-08-03T13:30:03+00:00")
        self.assertIsNotNone(naive)
        self.assertIsNotNone(aware)
        self.assertEqual(naive, aware)  # must not raise comparing them

    def test_z_suffix_parses(self):
        self.assertIsNotNone(cpm._parse_dt("2026-08-03T13:30:03Z"))

    def test_none_and_malformed_return_none(self):
        self.assertIsNone(cpm._parse_dt(None))
        self.assertIsNone(cpm._parse_dt("not a date"))


class TestRivalryKey(unittest.TestCase):
    def test_same_rivalry_groups_regardless_of_incoming_existing_order(self):
        r1 = _record(incoming_vendor="vendor-a", existing_vendor="vendor-b")
        r2 = _record(incoming_vendor="vendor-b", existing_vendor="vendor-a")
        self.assertEqual(cpm._rivalry_key(r1), cpm._rivalry_key(r2))

    def test_missing_fields_return_none(self):
        self.assertIsNone(cpm._rivalry_key({"brand_id": "brand-x"}))


class TestSummarize(unittest.TestCase):
    def test_counts_by_resolution(self):
        records = [
            _record(resolution="recorded_alongside"),
            _record(resolution="recorded_alongside"),
            _record(resolution="auto_superseded"),
        ]
        summary = cpm.summarize(records)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_resolution"], {"recorded_alongside": 2, "auto_superseded": 1})


class TestRecurringRivalries(unittest.TestCase):
    def test_below_min_count_is_not_flagged(self):
        records = [
            _record(detected_at="2026-09-01T00:00:00+00:00"),
            _record(detected_at="2026-09-10T00:00:00+00:00"),
        ]
        findings = cpm.recurring_rivalries(records, min_count=3, min_span_days=3)
        self.assertEqual(findings, [])

    def test_below_min_span_is_not_flagged_even_with_enough_count(self):
        """Many occurrences logged within the same hour (e.g. a single
        pipeline run retrying) is not the same signal as genuinely
        recurring across real elapsed time."""
        records = [_record(detected_at=f"2026-09-01T00:0{i}:00+00:00") for i in range(5)]
        findings = cpm.recurring_rivalries(records, min_count=3, min_span_days=3)
        self.assertEqual(findings, [])

    def test_real_recurring_rivalry_is_flagged(self):
        records = [
            _record(brand_name="Blaze Pizza", category="pos",
                     incoming_vendor="vendor-qu", incoming_name="Qu",
                     existing_vendor="vendor-oracle", existing_name="Oracle",
                     detected_at=f"2026-09-{25+i:02d}T00:00:00+00:00")
            for i in range(5)
        ]
        findings = cpm.recurring_rivalries(records, min_count=3, min_span_days=3)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["brand_name"], "Blaze Pizza")
        self.assertEqual(f["occurrences"], 5)
        self.assertEqual(sorted(f["vendors"]), ["Oracle", "Qu"])

    def test_auto_superseded_records_are_not_counted_as_unresolved(self):
        """A rivalry that got cleanly auto-superseded isn't "unresolved" --
        only recorded_alongside (the don't-pick-a-winner outcome) counts."""
        records = [
            _record(resolution="auto_superseded", detected_at=f"2026-09-{d:02d}T00:00:00+00:00")
            for d in (1, 5, 10)
        ]
        findings = cpm.recurring_rivalries(records, min_count=2, min_span_days=1)
        self.assertEqual(findings, [])

    def test_distinct_rivalries_do_not_merge(self):
        records = [
            _record(brand_id="brand-a", detected_at=f"2026-09-{d:02d}T00:00:00+00:00") for d in (1, 5, 10)
        ] + [
            _record(brand_id="brand-b", detected_at=f"2026-09-{d:02d}T00:00:00+00:00") for d in (1, 5, 10)
        ]
        findings = cpm.recurring_rivalries(records, min_count=3, min_span_days=3)
        self.assertEqual(len(findings), 2)

    def test_sorted_most_recurring_first(self):
        frequent = [
            _record(brand_id="brand-frequent", detected_at=f"2026-09-{d:02d}T00:00:00+00:00") for d in range(1, 10)
        ]
        rare = [
            _record(brand_id="brand-rare", detected_at=f"2026-09-{d:02d}T00:00:00+00:00") for d in (1, 5, 10)
        ]
        findings = cpm.recurring_rivalries(frequent + rare, min_count=3, min_span_days=3)
        self.assertEqual(findings[0]["brand_id"], "brand-frequent")


class TestStuckLegacyRequiresConfirmation(unittest.TestCase):
    def test_filters_only_requires_confirmation(self):
        records = [
            _record(resolution="requires_confirmation"),
            _record(resolution="recorded_alongside"),
            _record(resolution="auto_superseded"),
        ]
        stuck = cpm.stuck_legacy_requires_confirmation(records)
        self.assertEqual(len(stuck), 1)

    def test_empty_when_none_present(self):
        records = [_record(resolution="recorded_alongside")]
        self.assertEqual(cpm.stuck_legacy_requires_confirmation(records), [])


class TestCollectFindings(unittest.TestCase):
    def test_clean_log_produces_no_findings(self):
        tmp = Path(tempfile.mkdtemp()) / "log.jsonl"
        tmp.write_text(json.dumps(_record(resolution="auto_superseded")) + "\n", encoding="utf-8")
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(cpm, "CONFLICT_LOG_PATH", tmp):
            findings = cpm.collect_findings()
        self.assertEqual(findings, [])

    def test_recurring_rivalry_and_stuck_legacy_both_surface(self):
        tmp = Path(tempfile.mkdtemp()) / "log.jsonl"
        records = [
            _record(brand_name="Blaze Pizza", resolution="recorded_alongside",
                     detected_at=f"2026-09-{d:02d}T00:00:00+00:00")
            for d in (1, 5, 10)
        ] + [_record(resolution="requires_confirmation", detected_at="2026-08-01T00:00:00+00:00")]
        tmp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(cpm, "CONFLICT_LOG_PATH", tmp):
            findings = cpm.collect_findings()
        self.assertEqual(len(findings), 2)
        self.assertTrue(any("Blaze Pizza" in f for f in findings))
        self.assertTrue(any("requires_confirmation" in f for f in findings))


if __name__ == "__main__":
    unittest.main()
