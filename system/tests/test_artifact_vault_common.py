#!/usr/bin/env python3
"""
test_artifact_vault_common.py — register_version()'s no-op dedup guard
(2026-09-25, added to support routine/scheduled regeneration -- see
refresh_persisted_briefs.py). Before this guard, every call created a new
version and archived the prior "current" file, even when the rendered
content was byte-identical to what was already there -- fine for an
on-demand, human-triggered regenerate click, but would have meant 365
near-duplicate versions a year for a document a daily job re-renders
unconditionally, burying any real change under noise.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import artifact_vault_common as avc  # noqa: E402


class TestRegisterVersionNoOpGuard(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._patch = patch.object(avc, "VAULT_ROOT", Path(self.tmpdir.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmpdir.cleanup()

    def test_first_call_creates_version_1(self):
        entry = avc.register_version("widgets", "Widget", "acme", "Acme", "content v1")
        self.assertEqual(entry["version"], 1)
        self.assertFalse(entry["superseded"])

    def test_identical_content_returns_existing_entry_not_a_new_version(self):
        r1 = avc.register_version("widgets", "Widget", "acme", "Acme", "content v1", generated_for="todd")
        r2 = avc.register_version("widgets", "Widget", "acme", "Acme", "content v1", generated_for="someone-else")
        self.assertEqual(r1["version"], 1)
        self.assertEqual(r2["version"], 1)
        self.assertEqual(r1, r2)

        current = avc.get_current_version("widgets", "acme")
        self.assertEqual(current["version"], 1)

    def test_identical_content_does_not_write_to_history(self):
        avc.register_version("widgets", "Widget", "acme", "Acme", "content v1")
        avc.register_version("widgets", "Widget", "acme", "Acme", "content v1")
        history_dir = avc.instance_dir("widgets", "acme") / "history"
        self.assertEqual(list(history_dir.glob("*")), [])

    def test_real_content_change_creates_a_genuinely_new_version(self):
        r1 = avc.register_version("widgets", "Widget", "acme", "Acme", "content v1")
        r2 = avc.register_version("widgets", "Widget", "acme", "Acme", "content v2 -- real change")
        self.assertEqual(r1["version"], 1)
        self.assertEqual(r2["version"], 2)

        current = avc.get_current_version("widgets", "acme", include_content=True)
        self.assertEqual(current["version"], 2)
        self.assertEqual(current["content"], "content v2 -- real change")

    def test_real_content_change_archives_the_prior_version_to_history(self):
        avc.register_version("widgets", "Widget", "acme", "Acme", "content v1")
        avc.register_version("widgets", "Widget", "acme", "Acme", "content v2")
        history_dir = avc.instance_dir("widgets", "acme") / "history"
        archived = list(history_dir.glob("*"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_text(encoding="utf-8"), "content v1")

    def test_change_then_revert_creates_a_third_version_not_a_no_op(self):
        """A -> B -> A is three real transitions, not a no-op on the third
        call -- the guard only compares against the immediately-current
        version, never the full history, so oscillating content is never
        silently collapsed."""
        r1 = avc.register_version("widgets", "Widget", "acme", "Acme", "content v1")
        r2 = avc.register_version("widgets", "Widget", "acme", "Acme", "content v2")
        r3 = avc.register_version("widgets", "Widget", "acme", "Acme", "content v1")
        self.assertEqual([r1["version"], r2["version"], r3["version"]], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
