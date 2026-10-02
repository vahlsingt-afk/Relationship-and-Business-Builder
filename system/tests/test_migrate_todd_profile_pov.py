"""
test_migrate_todd_profile_pov.py — User POV Registry Phase 1 seed
migration from system/00_TODD_PROFILE.md.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import user_pov as up  # noqa: E402
import migrate_todd_profile_pov as m  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig = {
            "ROOT": up.ROOT, "REGISTRY_PATH": up.REGISTRY_PATH,
            "EVENTS_PATH": up.EVENTS_PATH, "EVIDENCE_LINKS_PATH": up.EVIDENCE_LINKS_PATH,
        }
        up.ROOT = tmp
        up.REGISTRY_PATH = tmp / "registry.json"
        up.EVENTS_PATH = tmp / "events.jsonl"
        up.EVIDENCE_LINKS_PATH = tmp / "evidence_links.jsonl"

    def tearDown(self):
        for name, path in self._orig.items():
            setattr(up, name, path)
        self._tmpdir.cleanup()


class TestRun(_IsolatedRootMixin, unittest.TestCase):
    def test_creates_all_seed_entries(self):
        summary = m.run(dry_run=False)
        self.assertEqual(summary["created"], len(m._SEED_ENTRIES))
        self.assertEqual(summary["skipped_existing"], 0)
        entries = up.list_pov_entries()
        self.assertEqual(len(entries), len(m._SEED_ENTRIES))

    def test_every_entry_is_user_authored_and_never_needs_review(self):
        m.run(dry_run=False)
        for e in up.list_pov_entries():
            self.assertEqual(e["authorship"], "user_authored")
            self.assertFalse(e["needs_review"])
            self.assertEqual(e["source_document"], m.SOURCE_DOCUMENT)

    def test_idempotent_on_rerun(self):
        m.run(dry_run=False)
        summary2 = m.run(dry_run=False)
        self.assertEqual(summary2["created"], 0)
        self.assertEqual(summary2["skipped_existing"], len(m._SEED_ENTRIES))
        self.assertEqual(len(up.list_pov_entries()), len(m._SEED_ENTRIES))

    def test_dry_run_writes_nothing(self):
        summary = m.run(dry_run=True)
        self.assertEqual(len(summary["entries"]), len(m._SEED_ENTRIES))
        self.assertEqual(up.list_pov_entries(), [])

    def test_hard_boundary_entries_present(self):
        m.run(dry_run=False)
        boundaries = up.list_pov_entries(entry_type="hard_boundary")
        self.assertEqual(len(boundaries), 2)
        for b in boundaries:
            self.assertEqual(b["scope"], "business_development")

    def test_all_seed_vocab_is_valid(self):
        """Every hardcoded (type, conviction) pair in _SEED_ENTRIES must be
        a real value user_pov.py accepts -- catches a typo in the seed
        list itself before it ever reaches add_pov_entry live."""
        for statement, entry_type, scope, conviction, source_section in m._SEED_ENTRIES:
            self.assertIn(entry_type, up.VALID_TYPES)
            self.assertIn(conviction, up.VALID_CONVICTIONS)
            self.assertTrue(statement.strip())


if __name__ == "__main__":
    unittest.main()
