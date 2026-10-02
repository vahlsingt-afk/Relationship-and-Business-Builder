#!/usr/bin/env python3
"""test_ecosystem_daily_pipeline.py — RB Unified Restaurant-Tech Graph
(2026-07-31), Phase 5: Daily Mutation Workflow Wiring.

Covers intelligence_mutation_engine.py's refresh_ecosystem_daily() /
generate_mutations_from_signal_row(), which turn today's already-classified
earnings/trade-press signal rows (Phase 4's provider_win /
contract_renewal_expansion / vendor_churn_loss vocabulary) into
ecosystem_intelligence.json mutations.

Every test in this file that touches disk uses _patch_engine_paths() to
redirect EVERY relevant module-level path constant to a tmp_path sandbox
before calling anything -- core.SYSTEM_DIR/core.INBOX_DIR/core.CACHE_DIR are
bound to the real filesystem at rb_core.py's import time, so patching
core.SYSTEM_DIR alone does NOT retroactively change constants already
derived from it elsewhere. This is not a hypothetical concern: an earlier
draft of this phase patched only core.SYSTEM_DIR and, via
eco._write_graph()'s own stale-bound constants, briefly overwrote the real
production system/ecosystem_intelligence.json during test development. Every
constant this module writes through (EARNINGS_SIGNALS_PATH,
MARKET_FEED_SIGNALS_PATH, ECOSYSTEM_MUTATION_RECEIPT_PATH, MUTATION_LOG_PATH,
EXEC_POV_PATH, ENGAGEMENT_OPPORTUNITIES_PATH, core.BASELINE_PATH) must be
patched explicitly, by name, every time.

Test IDs:
  EDP1 — generate_mutations_from_signal_row: named win produces a mutation
  EDP2 — unnamed win produces no mutation (never auto-promoted to a named relationship)
  EDP3 — row with a non-lifecycle signal_type produces no mutation
  EDP4 — row naming no known vendor produces no mutation
  EDP5 — renewal row tagged lifecycle_update; churn row tagged supersedes + requires review
  EDP6 — refresh_ecosystem_daily: end-to-end, real files never touched, receipt written
  EDP7 — idempotency: rerunning against unchanged rows applies zero new mutations
  EDP8 — malformed JSONL lines are skipped, not fatal
  EDP9 — a pipeline-level exception is caught and reported in the receipt, not raised
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import intelligence_mutation_engine as ime  # noqa: E402


def _empty_ecosystem() -> dict:
    return {"entities": [], "relationships": []}


def _patch_engine_paths(test: unittest.TestCase, tmp_path: Path) -> None:
    """Redirect every path intelligence_mutation_engine.py's daily-pipeline
    functions read or write to a tmp sandbox, and restore the originals on
    teardown regardless of pass/fail."""
    system_dir = tmp_path / "system"
    cache_dir = system_dir / ".cache"
    inbox_dir = system_dir / "inbox"
    for d in (system_dir, cache_dir, inbox_dir):
        d.mkdir(parents=True, exist_ok=True)

    baseline_path = system_dir / "baseline_index.json"
    baseline_path.write_text("[]", encoding="utf-8")
    (system_dir / "strategic_memory.json").write_text('{"signals":[]}', encoding="utf-8")

    originals = {
        "core.SYSTEM_DIR": ime.core.SYSTEM_DIR,
        "core.BASELINE_PATH": ime.core.BASELINE_PATH,
        "MUTATION_LOG_PATH": ime.MUTATION_LOG_PATH,
        "EXEC_POV_PATH": ime.EXEC_POV_PATH,
        "ENGAGEMENT_OPPORTUNITIES_PATH": ime.ENGAGEMENT_OPPORTUNITIES_PATH,
        "EARNINGS_SIGNALS_PATH": ime.EARNINGS_SIGNALS_PATH,
        "MARKET_FEED_SIGNALS_PATH": ime.MARKET_FEED_SIGNALS_PATH,
        "ECOSYSTEM_MUTATION_RECEIPT_PATH": ime.ECOSYSTEM_MUTATION_RECEIPT_PATH,
    }

    def _restore():
        ime.core.SYSTEM_DIR = originals["core.SYSTEM_DIR"]
        ime.core.BASELINE_PATH = originals["core.BASELINE_PATH"]
        ime.MUTATION_LOG_PATH = originals["MUTATION_LOG_PATH"]
        ime.EXEC_POV_PATH = originals["EXEC_POV_PATH"]
        ime.ENGAGEMENT_OPPORTUNITIES_PATH = originals["ENGAGEMENT_OPPORTUNITIES_PATH"]
        ime.EARNINGS_SIGNALS_PATH = originals["EARNINGS_SIGNALS_PATH"]
        ime.MARKET_FEED_SIGNALS_PATH = originals["MARKET_FEED_SIGNALS_PATH"]
        ime.ECOSYSTEM_MUTATION_RECEIPT_PATH = originals["ECOSYSTEM_MUTATION_RECEIPT_PATH"]

    test.addCleanup(_restore)

    ime.core.SYSTEM_DIR = system_dir
    ime.core.BASELINE_PATH = baseline_path
    ime.MUTATION_LOG_PATH = cache_dir / "knowledge_mutations.json"
    ime.EXEC_POV_PATH = cache_dir / "executive_povs.json"
    ime.ENGAGEMENT_OPPORTUNITIES_PATH = cache_dir / "engagement_opportunities.json"
    ime.EARNINGS_SIGNALS_PATH = inbox_dir / "market_signals_earnings.jsonl"
    ime.MARKET_FEED_SIGNALS_PATH = inbox_dir / "market_signals_feed.jsonl"
    ime.ECOSYSTEM_MUTATION_RECEIPT_PATH = cache_dir / "ecosystem_mutation_receipt.json"


def _write_ecosystem(graph: dict) -> None:
    ime.core.SYSTEM_DIR.joinpath("ecosystem_intelligence.json").write_text(json.dumps(graph))


def _win_row(**overrides) -> dict:
    row = {
        "title": "Toast Announces Wendy's Selects Toast as Exclusive POS Provider",
        "url": "https://example.com/1",
        "published_at": "2026-07-31",
        "company": "Wendy's",
        "category": "pos",
        "signal_type": "provider_win",
        "confidence": "high",
        "pain_point_or_priority": "",
    }
    row.update(overrides)
    return row


class EDP1_NamedWin(unittest.TestCase):
    def test_named_win_produces_mutation(self):
        result = ime.generate_mutations_from_signal_row(
            _win_row(), ecosystem=_empty_ecosystem(), source_label="earnings_monitor",
        )
        self.assertEqual(len(result["mutations"]), 1)
        mut = result["mutations"][0]
        self.assertEqual(mut["to_entity_id"], "vendor-toast")
        self.assertEqual(mut["reconciliation_outcome"], "new_relationship")
        self.assertIn(mut["id"], [m["id"] for m in result["auto_applied_mutations"]])


class EDP2_UnnamedWinNeverPromoted(unittest.TestCase):
    def test_unnamed_win_produces_no_mutation(self):
        result = ime.generate_mutations_from_signal_row(
            _win_row(company="", title="A major QSR operator selects a new POS provider"),
            ecosystem=_empty_ecosystem(), source_label="market_source_feeds",
        )
        self.assertEqual(result["mutations"], [])


class EDP3_NonLifecycleSignalIgnored(unittest.TestCase):
    def test_earnings_release_row_produces_no_mutation(self):
        result = ime.generate_mutations_from_signal_row(
            _win_row(signal_type="earnings_release"),
            ecosystem=_empty_ecosystem(), source_label="earnings_monitor",
        )
        self.assertEqual(result["mutations"], [])


class EDP4_NoKnownVendorNamed(unittest.TestCase):
    def test_row_with_no_recognized_vendor_produces_no_mutation(self):
        result = ime.generate_mutations_from_signal_row(
            _win_row(title="Wendy's selects a brand-new unlisted POS startup"),
            ecosystem=_empty_ecosystem(), source_label="earnings_monitor",
        )
        self.assertEqual(result["mutations"], [])


class EDP5_RenewalAndChurnTagging(unittest.TestCase):
    def test_renewal_tagged_lifecycle_update_and_auto_applied(self):
        result = ime.generate_mutations_from_signal_row(
            _win_row(
                signal_type="contract_renewal_expansion",
                title="PAR Technology renews multi-year agreement with Wendy's",
            ),
            ecosystem=_empty_ecosystem(), source_label="earnings_monitor",
        )
        self.assertEqual(len(result["mutations"]), 1)
        mut = result["mutations"][0]
        self.assertEqual(mut["reconciliation_outcome"], "lifecycle_update")
        self.assertFalse(mut["requires_confirmation"])

    def test_churn_tagged_supersedes_and_requires_review_even_at_high_confidence(self):
        result = ime.generate_mutations_from_signal_row(
            _win_row(
                signal_type="vendor_churn_loss",
                title="Wendy's replaces Presto with Hi Auto for drive-thru voice AI",
            ),
            ecosystem=_empty_ecosystem(), source_label="earnings_monitor",
        )
        self.assertEqual(len(result["mutations"]), 2)
        vendor_ids = {m["to_entity_id"] for m in result["mutations"]}
        self.assertEqual(vendor_ids, {"vendor-presto", "vendor-hi-auto"})
        for mut in result["mutations"]:
            self.assertEqual(mut["reconciliation_outcome"], "supersedes_existing_relationship")
            self.assertTrue(mut["requires_confirmation"])
        self.assertEqual(result["auto_applied_mutations"], [])


class EDP6_EndToEndRealFilesUntouched(unittest.TestCase):
    def test_refresh_ecosystem_daily_writes_only_to_sandbox(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _patch_engine_paths(self, tmp_path)
            _write_ecosystem(_empty_ecosystem())
            ime.EARNINGS_SIGNALS_PATH.write_text(json.dumps(_win_row()) + "\n", encoding="utf-8")
            ime.MARKET_FEED_SIGNALS_PATH.write_text("", encoding="utf-8")

            receipt = ime.refresh_ecosystem_daily(dry_run=False)

            self.assertEqual(receipt["status"], "ok")
            self.assertEqual(receipt["rows_processed"], 1)
            self.assertEqual(receipt["mutations_generated"], 1)
            self.assertEqual(receipt["applied"], 1)
            self.assertTrue(ime.ECOSYSTEM_MUTATION_RECEIPT_PATH.exists())

            graph = json.loads(ime.core.SYSTEM_DIR.joinpath("ecosystem_intelligence.json").read_text())
            rel_vendor_ids = {r["to_entity_id"] for r in graph["relationships"]}
            self.assertIn("vendor-toast", rel_vendor_ids)


class EDP7_Idempotency(unittest.TestCase):
    def test_rerun_against_unchanged_rows_applies_nothing_new(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _patch_engine_paths(self, tmp_path)
            _write_ecosystem(_empty_ecosystem())
            ime.EARNINGS_SIGNALS_PATH.write_text(json.dumps(_win_row()) + "\n", encoding="utf-8")
            ime.MARKET_FEED_SIGNALS_PATH.write_text("", encoding="utf-8")

            first = ime.refresh_ecosystem_daily(dry_run=False)
            second = ime.refresh_ecosystem_daily(dry_run=False)

            self.assertEqual(first["applied"], 1)
            self.assertEqual(second["applied"], 0, "rerunning on unchanged input must not duplicate mutations")


class EDP8_MalformedLinesSkipped(unittest.TestCase):
    def test_malformed_jsonl_line_does_not_crash_the_run(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _patch_engine_paths(self, tmp_path)
            _write_ecosystem(_empty_ecosystem())
            ime.EARNINGS_SIGNALS_PATH.write_text(
                "not valid json\n" + json.dumps(_win_row()) + "\n", encoding="utf-8",
            )
            ime.MARKET_FEED_SIGNALS_PATH.write_text("", encoding="utf-8")

            receipt = ime.refresh_ecosystem_daily(dry_run=False)
            self.assertEqual(receipt["status"], "ok")
            self.assertEqual(receipt["rows_processed"], 1)  # malformed line silently dropped, not counted


class EDP9_NeverFailsTheMorningBrief(unittest.TestCase):
    def test_exception_is_caught_and_reported_not_raised(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _patch_engine_paths(self, tmp_path)
            # No ecosystem_intelligence.json written at all -- _load_ecosystem()
            # tolerates this (returns {}), but leave EARNINGS_SIGNALS_PATH
            # pointing at a directory (not a file) to force a real read error
            # inside the try/except, proving the pipeline degrades gracefully.
            ime.EARNINGS_SIGNALS_PATH.mkdir(parents=True, exist_ok=True)
            ime.MARKET_FEED_SIGNALS_PATH.write_text("", encoding="utf-8")

            receipt = ime.refresh_ecosystem_daily(dry_run=False)  # must not raise
            self.assertEqual(receipt["status"], "failed")
            self.assertIn("error", receipt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
