#!/usr/bin/env python3
"""
test_relationship_card.py — RB-2026-09-08.

Isolated against a disposable rb_core.BASELINE_PATH + rb_core.CARDS_DIR
(a fixture RC contact + card file) and disposable artifact_vault_common.
VAULT_ROOT / intelligence_index paths. Never touches real project state.

rb_core.load_baseline(path=BASELINE_PATH) binds its default at import time
-- relationship_card.py always passes core.BASELINE_PATH explicitly at the
call site (see its own docstring), so patching the module-level attribute
here is enough for real isolation; never assert against the bare-default
call path.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import relationship_card as rcm  # noqa: E402
import rb_core as core  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

RC_ID = "test-fixture-rc"
VC_ID = "test-fixture-vc"
CARD_TEXT = """---
id: test-fixture-rc
name: Test Fixture RC
state: ACTIVE
tier: inner
trust_state: stable
momentum: positive
created: 2026-09-01
last_touch: 2026-09-01
linkedin_url: null
current_company: Test Co
current_role: Tester
circles: []
tags: []
---

# Test Fixture RC

## Why this matters
Real hand-authored reason this relationship matters.

## Open loops
- A real open loop for the fixture contact.
"""


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_baseline_path = core.BASELINE_PATH
        self._orig_cards_dir = core.CARDS_DIR
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH

        core.BASELINE_PATH = tmp_root / "baseline_index.json"
        core.CARDS_DIR = tmp_root / "cards"
        avc.VAULT_ROOT = tmp_root / "artifact_vault"
        ix.INDEX_PATH = tmp_root / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = tmp_root / "intelligence_index_updates.jsonl"

        core.CARDS_DIR.mkdir(parents=True)
        (core.CARDS_DIR / f"{RC_ID}.md").write_text(CARD_TEXT, encoding="utf-8")

        core.BASELINE_PATH.write_text(json.dumps([
            {"id": RC_ID, "name": "Test Fixture RC", "signal_class": "RC",
             "rc_tier": "inner", "rc_state": "ACTIVE", "sources": ["test"],
             "last_touch": "2026-09-01"},
            {"id": VC_ID, "name": "Test Fixture VC", "signal_class": "VC",
             "rc_tier": None, "rc_state": None, "sources": ["test"], "last_touch": None},
        ]), encoding="utf-8")

    def tearDown(self):
        core.BASELINE_PATH = self._orig_baseline_path
        core.CARDS_DIR = self._orig_cards_dir
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestPublishRelationshipCard(_IsolatedFixtureMixin):
    def test_publishes_verbatim_snapshot(self):
        result = rcm.publish_relationship_card(RC_ID)
        self.assertEqual(result["markdown"], CARD_TEXT)
        self.assertEqual(result["version"]["version"], 1)

    def test_never_mutates_the_real_card_file(self):
        before = (core.CARDS_DIR / f"{RC_ID}.md").read_text()
        rcm.publish_relationship_card(RC_ID)
        after = (core.CARDS_DIR / f"{RC_ID}.md").read_text()
        self.assertEqual(before, after)

    def test_never_mutates_baseline(self):
        before = core.BASELINE_PATH.read_text()
        rcm.publish_relationship_card(RC_ID)
        after = core.BASELINE_PATH.read_text()
        self.assertEqual(before, after)

    def test_rejects_non_rc_contact(self):
        with self.assertRaises(ValueError):
            rcm.publish_relationship_card(VC_ID)

    def test_rejects_unknown_contact(self):
        with self.assertRaises(FileNotFoundError):
            rcm.publish_relationship_card("does-not-exist-xyz")

    def test_rejects_rc_contact_with_no_card_file(self):
        core.BASELINE_PATH.write_text(json.dumps([
            {"id": "rc-no-card", "name": "No Card RC", "signal_class": "RC",
             "rc_tier": "broader", "rc_state": "ACTIVE", "sources": ["test"]},
        ]), encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            rcm.publish_relationship_card("rc-no-card")

    def test_republishing_creates_new_version(self):
        r1 = rcm.publish_relationship_card(RC_ID)
        (core.CARDS_DIR / f"{RC_ID}.md").write_text(CARD_TEXT + "\n## Risks\nA new risk noted later.\n", encoding="utf-8")
        r2 = rcm.publish_relationship_card(RC_ID)
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 2)
        current = rcm.get_current_relationship_card(RC_ID, include_content=True)
        self.assertIn("A new risk noted later.", current["content"])

    def test_registers_in_intelligence_index_on_first_version(self):
        rcm.publish_relationship_card(RC_ID)
        matches = ix.find("Test Fixture RC")
        rc_matches = [m for m in matches if m.get("resource_type") == "relationship_card"]
        self.assertEqual(len(rc_matches), 1)

    def test_publish_all_skips_rc_with_no_card(self):
        core.BASELINE_PATH.write_text(json.dumps([
            {"id": RC_ID, "name": "Test Fixture RC", "signal_class": "RC",
             "rc_tier": "inner", "rc_state": "ACTIVE", "sources": ["test"]},
            {"id": "rc-no-card", "name": "No Card RC", "signal_class": "RC",
             "rc_tier": "broader", "rc_state": "ACTIVE", "sources": ["test"]},
            {"id": VC_ID, "name": "Test Fixture VC", "signal_class": "VC",
             "rc_tier": None, "rc_state": None, "sources": ["test"]},
        ]), encoding="utf-8")
        results = rcm.publish_all_relationship_cards()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["contact_id"], RC_ID)


if __name__ == "__main__":
    unittest.main()
