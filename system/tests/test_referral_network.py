#!/usr/bin/env python3
"""
test_referral_network.py — RB-2026-09-08.

Isolated against a disposable rb_core.SYSTEM_DIR (for intro_brokers.md --
read fresh at call time, so patching this constant is enough), a disposable
rb_core.BASELINE_PATH + rb_core.ACTIVE_THREADS_PATH, and disposable
artifact_vault_common.VAULT_ROOT / intelligence_index paths. Never touches
real project state. See relationship_card.py's own docstring for why
core.BASELINE_PATH (not a bare call) is the correct isolation point --
find_intro_paths() has the identical stale-default-binding risk internally.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import referral_network as rn  # noqa: E402
import rb_core as core  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

OVERVIEW_TEXT = "# Referral Brokers\n\n## Hospitality\n\n| Broker | RC tier | DRR |\n|---|---|---|\n| Test Broker | inner | 90.0 |\n"


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_system_dir = core.SYSTEM_DIR
        self._orig_baseline_path = core.BASELINE_PATH
        self._orig_threads_path = core.ACTIVE_THREADS_PATH
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH

        core.SYSTEM_DIR = tmp_root
        core.BASELINE_PATH = tmp_root / "baseline_index.json"
        core.ACTIVE_THREADS_PATH = tmp_root / "active_threads.yaml"
        avc.VAULT_ROOT = tmp_root / "artifact_vault"
        ix.INDEX_PATH = tmp_root / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = tmp_root / "intelligence_index_updates.jsonl"

        (tmp_root / "intro_brokers.md").write_text(OVERVIEW_TEXT, encoding="utf-8")

        core.BASELINE_PATH.write_text(json.dumps([
            {"id": "insider-at-target", "name": "Insider At Target", "signal_class": "LKI",
             "current_company": "Test Target Co", "sources": ["test"], "last_touch": "2026-09-01",
             "circles": [], "email": "insider@test.co", "phone": None},
            {"id": "another-insider", "name": "Another Insider", "signal_class": "VC",
             "current_company": "Test Target Co", "sources": ["test"], "last_touch": None,
             "circles": [], "email": None, "phone": None},
        ]), encoding="utf-8")

    def tearDown(self):
        core.SYSTEM_DIR = self._orig_system_dir
        core.BASELINE_PATH = self._orig_baseline_path
        core.ACTIVE_THREADS_PATH = self._orig_threads_path
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestReferralNetworkOverview(_IsolatedFixtureMixin):
    def test_publishes_verbatim_snapshot(self):
        result = rn.publish_referral_network_overview()
        self.assertEqual(result["markdown"], OVERVIEW_TEXT)
        self.assertEqual(result["version"]["version"], 1)

    def test_never_mutates_intro_brokers_file(self):
        before = (core.SYSTEM_DIR / "intro_brokers.md").read_text()
        rn.publish_referral_network_overview()
        after = (core.SYSTEM_DIR / "intro_brokers.md").read_text()
        self.assertEqual(before, after)

    def test_raises_when_file_missing(self):
        (core.SYSTEM_DIR / "intro_brokers.md").unlink()
        with self.assertRaises(FileNotFoundError):
            rn.publish_referral_network_overview()

    def test_regenerating_creates_new_version(self):
        r1 = rn.publish_referral_network_overview()
        (core.SYSTEM_DIR / "intro_brokers.md").write_text(OVERVIEW_TEXT + "\n## Updated section\n", encoding="utf-8")
        r2 = rn.publish_referral_network_overview()
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 2)
        current = rn.get_current_referral_network_overview(include_content=True)
        self.assertIn("Updated section", current["content"])

    def test_registers_in_intelligence_index_on_first_version(self):
        rn.publish_referral_network_overview()
        matches = ix.find("Referral Network Overview")
        overview_matches = [m for m in matches if m.get("resource_type") == "referral_network_overview"]
        self.assertEqual(len(overview_matches), 1)


class TestReferralNetworkAnalysis(_IsolatedFixtureMixin):
    def test_renders_real_insiders_and_drr(self):
        md = rn.render_referral_network_analysis("Test Target Co")
        self.assertIn("Insider At Target", md)
        self.assertIn("Another Insider", md)

    def test_unknown_target_raises_without_persisting(self):
        with self.assertRaises(ValueError):
            rn.generate_referral_network_analysis("Totally Not A Real Company XYZ")
        self.assertIsNone(rn.get_current_referral_network_analysis("Totally Not A Real Company XYZ"))

    def test_generates_and_persists(self):
        result = rn.generate_referral_network_analysis("Test Target Co")
        self.assertEqual(result["version"]["version"], 1)
        current = rn.get_current_referral_network_analysis("Test Target Co", include_content=True)
        self.assertIn("Insider At Target", current["content"])

    def test_regenerating_unchanged_content_does_not_create_new_version(self):
        """artifact_vault_common.register_version()'s no-op guard
        (2026-09-25) -- unchanged content stays version 1 rather than
        bumping every time the daily refresh re-renders it. A real content
        change still versions correctly; see
        test_artifact_vault_common.py for that coverage directly."""
        r1 = rn.generate_referral_network_analysis("Test Target Co")
        r2 = rn.generate_referral_network_analysis("Test Target Co", generated_for="test-run")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 1)

    def test_registers_in_intelligence_index_on_first_version(self):
        rn.generate_referral_network_analysis("Test Target Co")
        matches = ix.find("Test Target Co")
        analysis_matches = [m for m in matches if m.get("resource_type") == "referral_network_analysis"]
        self.assertEqual(len(analysis_matches), 1)


if __name__ == "__main__":
    unittest.main()
