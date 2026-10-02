#!/usr/bin/env python3
"""
test_relationship_plan.py — RB-2026-09-08.

Isolated against a disposable rb_core.BASELINE_PATH + rb_core.CARDS_DIR and
disposable artifact_vault_common.VAULT_ROOT / intelligence_index paths.
Never touches real project state. See test_relationship_card.py's own
docstring for why core.BASELINE_PATH/core.CARDS_DIR (not a bare call) is
the correct isolation point.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import relationship_plan as rp  # noqa: E402
import rb_core as core  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

RC_WITH_CARD_ID = "test-fixture-rc-with-card"
LMI_NO_CARD_ID = "test-fixture-lmi-no-card"

CARD_TEXT = """---
id: test-fixture-rc-with-card
name: Test Fixture RC With Card
state: ACTIVE
tier: inner
trust_state: strained
momentum: negative
created: 2026-09-01
last_touch: 2026-09-01
current_company: Test Co
current_role: Tester
circles: []
tags: []
---

# Test Fixture RC With Card

## Why this matters
Real hand-authored reason this relationship matters, for context only.

## Open loops
- (none)
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
        (core.CARDS_DIR / f"{RC_WITH_CARD_ID}.md").write_text(CARD_TEXT, encoding="utf-8")

        core.BASELINE_PATH.write_text(json.dumps([
            {"id": RC_WITH_CARD_ID, "name": "Test Fixture RC With Card", "signal_class": "RC",
             "rc_tier": "inner", "rc_state": "ACTIVE", "sources": ["test"],
             "current_company": "Test Co", "current_role": "Tester"},
            {"id": LMI_NO_CARD_ID, "name": "Test Fixture LMI No Card", "signal_class": "LMI",
             "rc_tier": None, "rc_state": None, "sources": ["test"],
             "current_company": "Other Co", "current_role": "Someone"},
        ]), encoding="utf-8")

    def tearDown(self):
        core.BASELINE_PATH = self._orig_baseline_path
        core.CARDS_DIR = self._orig_cards_dir
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestGenerateRelationshipPlan(_IsolatedFixtureMixin):
    def test_rejects_empty_goal(self):
        with self.assertRaises(ValueError):
            rp.generate_relationship_plan(RC_WITH_CARD_ID, relationship_goal="  ", next_moves=["x"], status="active")

    def test_rejects_empty_next_moves(self):
        with self.assertRaises(ValueError):
            rp.generate_relationship_plan(RC_WITH_CARD_ID, relationship_goal="Real goal.", next_moves=[], status="active")

    def test_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            rp.generate_relationship_plan(RC_WITH_CARD_ID, relationship_goal="Real goal.", next_moves=["x"], status="not-a-status")

    def test_rejects_unknown_contact(self):
        with self.assertRaises(FileNotFoundError):
            rp.generate_relationship_plan("does-not-exist-xyz", relationship_goal="Real goal.", next_moves=["x"], status="active")

    def test_works_for_non_rc_contact(self):
        """Confirms the 'any contact, not RC-restricted' design decision --
        an LMI contact with no card at all must succeed, not raise."""
        result = rp.generate_relationship_plan(
            LMI_NO_CARD_ID, relationship_goal="Build toward RC over the next two quarters.",
            next_moves=["Find a warm reconnection reason."], status="active",
        )
        self.assertIn("Build toward RC", result["markdown"])
        self.assertIn("not yet a Relationship Card", result["markdown"])

    def test_pulls_real_card_context_when_present(self):
        result = rp.generate_relationship_plan(
            RC_WITH_CARD_ID, relationship_goal="Real goal.", next_moves=["x"], status="active",
        )
        self.assertIn("strained", result["markdown"])
        self.assertIn("negative", result["markdown"])
        self.assertIn("Real hand-authored reason this relationship matters", result["markdown"])

    def test_never_mutates_baseline(self):
        before = core.BASELINE_PATH.read_text()
        rp.generate_relationship_plan(RC_WITH_CARD_ID, relationship_goal="Real goal.", next_moves=["x"], status="active")
        after = core.BASELINE_PATH.read_text()
        self.assertEqual(before, after)

    def test_never_mutates_card_file(self):
        before = (core.CARDS_DIR / f"{RC_WITH_CARD_ID}.md").read_text()
        rp.generate_relationship_plan(RC_WITH_CARD_ID, relationship_goal="Real goal.", next_moves=["x"], status="active")
        after = (core.CARDS_DIR / f"{RC_WITH_CARD_ID}.md").read_text()
        self.assertEqual(before, after)

    def test_includes_target_cadence_when_given(self):
        result = rp.generate_relationship_plan(
            RC_WITH_CARD_ID, relationship_goal="Real goal.", next_moves=["x"], status="active", target_cadence_days=30,
        )
        self.assertIn("every 30 day(s)", result["markdown"])

    def test_regenerating_creates_new_version(self):
        r1 = rp.generate_relationship_plan(RC_WITH_CARD_ID, relationship_goal="First goal.", next_moves=["x"], status="active")
        r2 = rp.generate_relationship_plan(RC_WITH_CARD_ID, relationship_goal="Revised goal.", next_moves=["y"], status="paused")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 2)
        current = rp.get_current_relationship_plan(RC_WITH_CARD_ID, include_content=True)
        self.assertIn("Revised goal.", current["content"])

    def test_registers_in_intelligence_index_on_first_version(self):
        rp.generate_relationship_plan(RC_WITH_CARD_ID, relationship_goal="Real goal.", next_moves=["x"], status="active")
        matches = ix.find("Test Fixture RC With Card")
        rp_matches = [m for m in matches if m.get("resource_type") == "relationship_plan"]
        self.assertEqual(len(rp_matches), 1)


if __name__ == "__main__":
    unittest.main()
