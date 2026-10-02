#!/usr/bin/env python3
"""
test_inner_circle.py — RB-2026-09-08.

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

import inner_circle as ic  # noqa: E402
import rb_core as core  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402


def _card(contact_id, *, trust_state="stable", momentum="positive", open_loops="- (none)"):
    return f"""---
id: {contact_id}
name: Card For {contact_id}
state: ACTIVE
tier: inner
trust_state: {trust_state}
momentum: {momentum}
created: 2026-09-01
last_touch: 2026-09-01
current_company: Test Co
current_role: Tester
circles: []
tags: []
---

# Card For {contact_id}

## Open loops
{open_loops}
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
        (core.CARDS_DIR / "healthy-inner.md").write_text(_card("healthy-inner"), encoding="utf-8")
        (core.CARDS_DIR / "strained-inner.md").write_text(
            _card("strained-inner", trust_state="strained", open_loops="- A real overdue open loop."), encoding="utf-8",
        )
        (core.CARDS_DIR / "negative-momentum-inner.md").write_text(
            _card("negative-momentum-inner", momentum="negative"), encoding="utf-8",
        )
        (core.CARDS_DIR / "broader-contact.md").write_text(_card("broader-contact"), encoding="utf-8")

        core.BASELINE_PATH.write_text(json.dumps([
            {"id": "healthy-inner", "name": "Healthy Inner", "signal_class": "RC", "rc_tier": "inner",
             "current_company": "Test Co", "current_role": "Tester", "last_touch": "2026-09-01",
             "relationship_health": {"drr_score": 90.0}},
            {"id": "strained-inner", "name": "Strained Inner", "signal_class": "RC", "rc_tier": "inner",
             "current_company": "Test Co", "current_role": "Tester", "last_touch": "2026-01-01",
             "relationship_health": {"drr_score": 40.0}},
            {"id": "negative-momentum-inner", "name": "Negative Momentum Inner", "signal_class": "RC", "rc_tier": "inner",
             "current_company": "Test Co", "current_role": "Tester", "last_touch": "2026-02-01",
             "relationship_health": {"drr_score": 55.0}},
            # rc_tier == "broader" must NOT appear in Inner Circle at all.
            {"id": "broader-contact", "name": "Broader Contact", "signal_class": "RC", "rc_tier": "broader",
             "current_company": "Test Co", "current_role": "Tester", "last_touch": "2026-09-01",
             "relationship_health": {"drr_score": 99.0}},
            # No card file at all -- must render honestly, not crash.
            {"id": "no-card-inner", "name": "No Card Inner", "signal_class": "RC", "rc_tier": "inner",
             "current_company": "Test Co", "current_role": "Tester", "last_touch": None,
             "relationship_health": {}},
        ]), encoding="utf-8")

    def tearDown(self):
        core.BASELINE_PATH = self._orig_baseline_path
        core.CARDS_DIR = self._orig_cards_dir
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestRenderInnerCircle(_IsolatedFixtureMixin):
    def test_only_includes_inner_tier(self):
        md = ic.render_inner_circle()
        self.assertIn("Healthy Inner", md)
        self.assertNotIn("Broader Contact", md)

    def test_includes_drr_score_and_last_touch(self):
        md = ic.render_inner_circle()
        self.assertIn("90.0", md)
        self.assertIn("2026-09-01", md)

    def test_extracts_real_open_loops_verbatim(self):
        md = ic.render_inner_circle()
        self.assertIn("A real overdue open loop.", md)

    def test_handles_missing_card_file_honestly(self):
        md = ic.render_inner_circle()
        self.assertIn("No Card Inner", md)
        # "No Card Inner" appears twice in a row (heading, then repeated in
        # the body line) -- anchor on the full heading text so split() lands
        # after both occurrences, not between them.
        section = md.split("### No Card Inner")[1].split("### ")[0]
        self.assertIn("unknown", section)

    def test_needs_attention_includes_strained_and_negative_momentum(self):
        md = ic.render_inner_circle()
        attention = md.split("## Needs Attention")[1]
        self.assertIn("Strained Inner", attention)
        self.assertIn("Negative Momentum Inner", attention)
        self.assertNotIn("Healthy Inner", attention)

    def test_roster_includes_everyone_including_needs_attention(self):
        md = ic.render_inner_circle()
        roster = md.split("## Inner Circle Roster")[1].split("## Needs Attention")[0]
        self.assertIn("Healthy Inner", roster)
        self.assertIn("Strained Inner", roster)


class TestGenerateInnerCircle(_IsolatedFixtureMixin):
    def test_generates_and_persists(self):
        result = ic.generate_inner_circle()
        self.assertEqual(result["version"]["version"], 1)
        current = ic.get_current_inner_circle(include_content=True)
        self.assertIn("Healthy Inner", current["content"])

    def test_regenerating_unchanged_content_does_not_create_new_version(self):
        """artifact_vault_common.register_version()'s no-op guard
        (2026-09-25) -- unchanged content stays version 1 rather than
        bumping every time the daily refresh re-renders it. A real content
        change still versions correctly; see
        test_artifact_vault_common.py for that coverage directly."""
        r1 = ic.generate_inner_circle()
        r2 = ic.generate_inner_circle(generated_for="test-run")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 1)

    def test_never_mutates_baseline_or_cards(self):
        before_baseline = core.BASELINE_PATH.read_text()
        before_card = (core.CARDS_DIR / "healthy-inner.md").read_text()
        ic.generate_inner_circle()
        self.assertEqual(before_baseline, core.BASELINE_PATH.read_text())
        self.assertEqual(before_card, (core.CARDS_DIR / "healthy-inner.md").read_text())

    def test_registers_in_intelligence_index_on_first_version(self):
        ic.generate_inner_circle()
        matches = ix.find("Inner Circle")
        ic_matches = [m for m in matches if m.get("resource_type") == "inner_circle"]
        self.assertEqual(len(ic_matches), 1)


if __name__ == "__main__":
    unittest.main()
